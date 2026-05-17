import os
import json
import re
import requests
import urllib.parse
import hashlib
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HISTORY_FILE = "history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data if isinstance(data, list) else []
            except:
                return []
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def parse_post_data(post_element):
    # Intentar capturar por atributo nativo, si no, buscar divs con texto relevante
    text_elem = post_element.find(attrs={"data-ad-comet-preview": "post_message"})
    
    if not text_elem:
        for div in post_element.find_all('div', dir='auto'):
            div_text = div.get_text().lower()
            if "gratis" in div_text or "steam" in div_text or "epic" in div_text:
                text_elem = div
                break

    full_text = text_elem.get_text(separator="\n").strip() if text_elem else post_element.get_text(separator="\n").strip()
    
    # 1. Extraer URL (Decodificando el redireccionador de Facebook)
    url = "No encontrada"
    a_tags = post_element.find_all('a', href=True)
    for a in a_tags:
        href = a['href']
        if "l.facebook.com/l.php" in href:
            parsed_href = urllib.parse.urlparse(href)
            query_params = urllib.parse.parse_qs(parsed_href.query)
            if 'u' in query_params:
                potential_url = query_params['u'][0]
                if "facebook.com" not in potential_url:
                    url = potential_url
                    break
        elif "http" in href and "facebook.com" not in href:
            url = href
            break

    if url == "No encontrada":
        urls = re.findall(r'(https?://[^\s]+)', full_text)
        for u in urls:
            if "facebook.com" not in u:
                url = u.rstrip('.').rstrip('/')
                break

    # 2. Extraer Imagen del juego (Controlando Lazy Loading de Meta)
    image_url = None
    img_tags = post_element.find_all('img')
    for img in img_tags:
        src = img.get('data-src') or img.get('src') or ''
        if "http" in src and not any(x in src for x in ["emoji.php", "rsrc.php", "static.xx"]):
            image_url = src
            break

    # 3. Plataforma
    platform = "OTRA"
    lower_text = full_text.lower()
    if "steam" in url.lower() or "steam" in lower_text: 
        platform = "STEAM"
    elif "epic" in url.lower() or "epic" in lower_text: 
        platform = "EPIC GAMES"
    elif "gog" in url.lower() or "gog" in lower_text: 
        platform = "GOG"

    # 4. Nombre del Juego
    game = full_text.split('\n')[0] if full_text else "No detectado"
    game_match = re.search(r'^(.*?)\s+gratis en', full_text, re.IGNORECASE)
    if game_match: 
        game = game_match.group(1).strip()

    # 5. Tiempo / Vigencia de la oferta
    tiempo = "Hasta agotar existencias / No especificado"
    tiempo_match = re.search(r'((?:tienen\s+)?hasta\s+el\s+\d+\s+de\s+\w+|antes\s+del\s+\d+\s+de\s+\w+|gratis\s+por\s+tiempo\s+limitado|permanente)', full_text, re.IGNORECASE)
    if tiempo_match: 
        tiempo = tiempo_match.group(1).strip().capitalize()

    clean_text_id = re.sub(r'\s+', '', full_text[:80])
    post_id = hashlib.md5(clean_text_id.encode('utf-8')).hexdigest()

    return {
        "juego": game, "url": url, "plataforma": platform,
        "tiempo": tiempo, "imagen": image_url, "id": post_id,
        "raw_text": full_text.replace('\n', ' ')
    }

def send_to_discord(post, webhook_url):
    embed = {
        "title": "🎮 ¡Nuevo juego gratis detectado!",
        "color": 3066993, 
        "fields": [
            {"name": "Juego", "value": f"**{post['juego']}**", "inline": False},
            {"name": "Plataforma", "value": f"🔹 {post['plataforma']}", "inline": True},
            {"name": "Tiempo", "value": f"⏰ {post['tiempo']}", "inline": True},
            {"name": "Enlace de obtención", "value": post['url'], "inline": False}
        ],
        "footer": {"text": "@everyone"}
    }
    if post['imagen']: 
        embed["image"] = {"url": post['imagen']}
    
    payload = {"embeds": [embed]}
    res = requests.post(webhook_url, json=payload)
    return res.status_code

def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK")
    if not webhook_url:
        print("❌ Error: Variable DISCORD_WEBHOOK no configurada.")
        return

    history = load_history()
    new_history = list(history)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 900}
        )
        page = context.new_page()
        
        print("Abriendo Facebook...")
        page.goto("https://m.facebook.com/FreeSteamGamesJuegosSteamGratis", wait_until="networkidle")
        page.wait_for_timeout(3000)
        
        # --- GESTIÓN ANTIBLOQUEO: COOKIES EN INGLÉS ---
        print("Comprobando si aparece el aviso de cookies de Facebook...")
        botones_cookies = [
            "text='Allow all cookies'",
            "text='Allow essential and optional cookies'",
            "text='Accept all'",
            "button:has-text('Allow')",
            "button:has-text('Accept')"
        ]
        
        for selector_cookie in botones_cookies:
            boton = page.locator(selector_cookie).first
            if boton.is_visible():
                try:
                    print(f"🍪 Ventana de cookies detectada. Haciendo clic en: {selector_cookie}")
                    boton.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    break
                except:
                    pass

        try:
            page.keyboard.press("Escape")
        except:
            pass
        
        # Generar un buen scroll inicial para cargar bastantes publicaciones en el DOM
        print("Realizando scroll preventivo para cargar el feed...")
        for _ in range(5):
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(1200)

        # Localizar los contenedores de los posts de forma nativa con Playwright
        # Evaluamos los selectores móviles más estables de Meta
        selectores_post = ['div[role="article"]', 'div[data-tracking-duration-id]', 'article']
        locator_posts = None
        for sel in selectores_post:
            if page.locator(sel).count() > 0:
                locator_posts = page.locator(sel)
                break

        if not locator_posts:
            print("❌ No se encontraron estructuras de posts en la página.")
            browser.close()
            return

        total_posts = locator_posts.count()
        print(f"📦 Total de posts estructurales encontrados: {total_posts}")

        detected_new = False
        processed_count = 0

        # --- BUCLE DE PROCESAMIENTO UNO A UNO ---
        for i in range(total_posts):
            post_locator = locator_posts.nth(i)
            
            # 1. Intentar expandir el "See more" SOLO de este post específico antes de leer su HTML
            for sm_text in ["See more", "See more..."]:
                see_more_btn = post_locator.locator(f"text='{sm_text}'").filter(has_not_text="See more of").first
                if see_more_btn.is_visible():
                    try:
                        see_more_btn.scroll_into_view_if_needed(timeout=1000)
                        see_more_btn.click(timeout=1000)
                        page.wait_for_timeout(400)  # Pausa sutil para que se abra la descripción
                    except:
                        pass

            # 2. Extraer el HTML exclusivo de este post ya expandido
            post_html = post_locator.inner_html()
            p_soup = BeautifulSoup(post_html, "html.parser")
            
            # 3. Analizar los datos extraídos
            data = parse_post_data(p_soup)
            
            if len(data['raw_text']) < 15 or data['url'] == "No encontrada":
                continue
                
            processed_count += 1
            print(f"\n--- Analizando Post #{processed_count} ---")
            print(f"Juego: {data['juego']}")
            print(f"Tiempo: {data['tiempo']}")
            print(f"URL: {data['url']}")

            post_id = data['id']
            if post_id in history:
                print("🛑 Este juego ya está registrado en el historial.")
                continue

            print(f"🚀 ¡Enviando '{data['juego']}' a Discord!")
            status = send_to_discord(data, webhook_url)
            
            if status in [200, 204]:
                new_history.append(post_id)
                detected_new = True
            else:
                print(f"❌ Error Discord: {status}")

            if len(new_history) - len(history) >= 4:
                print("⚠️ Se alcanzó el límite preventivo de 4 envíos simultáneos.")
                break

        browser.close()

    if detected_new:
        save_history(new_history)
        print("\n✅ Historial actualizado con éxito en history.json.")
    else:
        print("\nNo se encontraron nuevas ofertas elegibles en esta ejecución.")

if __name__ == "__main__":
    main()

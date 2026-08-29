import os
import json
import re
import requests
import urllib.parse
import hashlib
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HISTORY_FILE = "history.json"
def es_url_valida(url):
    if not url or not url.startswith("http"):
        return False
    # Lista de dominios propios de Meta y redes a ignorar
    dominios_ignorados = [
        "facebook.com", "messenger.com", "fb.com", 
        "fb.me", "fbcdn.net", "instagram.com", "whatsapp.com"
    ]
    url_lower = url.lower()
    return not any(dominio in url_lower for dominio in dominios_ignorados)
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data if isinstance(data, list) else []
            except Exception:
                return []
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def parse_post_data(post_element):
    text_elem = post_element.find(attrs={"data-ad-comet-preview": "post_message"})
    
    if not text_elem:
        for div in post_element.find_all('div', dir='auto'):
            div_text = div.get_text().lower()
            if "gratis" in div_text or "steam" in div_text or "epic" in div_text:
                text_elem = div
                break

    full_text = text_elem.get_text(separator="\n").strip() if text_elem else post_element.get_text(separator="\n").strip()
    
    # 1. Extraer URL del post principal (si la hay)
    url = "No encontrada"
    a_tags = post_element.find_all('a', href=True)
    for a in a_tags:
        href = a['href']
        potential_url = None
        
        if "l.facebook.com/l.php" in href:
            parsed_href = urllib.parse.urlparse(href)
            query_params = urllib.parse.parse_qs(parsed_href.query)
            if 'u' in query_params:
                potential_url = urllib.parse.unquote(query_params['u'][0])
        else:
            potential_url = href

        if potential_url and es_url_valida(potential_url):
            url = potential_url
            break

    if url == "No encontrada":
        urls = re.findall(r'(https?://[^\s]+)', full_text)
        for u in urls:
            u_clean = u.rstrip('.').rstrip(')').rstrip('/')
            if es_url_valida(u_clean):
                url = u_clean
                break

    # 2. Extraer Imagen del juego (Controlando Proxies de Meta)
    image_url = None
    img_tags = post_element.find_all('img')
    for img in img_tags:
        src = img.get('data-src') or img.get('src') or ''
        if not src or any(x in src for x in ["emoji.php", "rsrc.php", "static.xx"]):
            continue
            
        if "safe_image.php" in src:
            parsed_src = urllib.parse.urlparse(src)
            query_params = urllib.parse.parse_qs(parsed_src.query)
            if 'url' in query_params:
                src = urllib.parse.unquote(query_params['url'][0])
        elif src.startswith('/'):
            src = f"https://m.facebook.com{src}"

        if src.startswith("http"):
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

    # 5. Tiempo
    tiempo = "Hasta agotar existencias / No especificado"
    patron_tiempo = (
        r'(?:tienen\s+)?hasta\s+(?:el\s+)?'
        r'(?:\d{1,2}\s+de\s+[a-záéíóúñ]+|\d{1,2}[/-]\d{1,2}|mañana|hoy)'
        r'(?:\s+a\s+las\s+\d{1,2}(?::\d{2})?)?'
        r'|gratis\s+(?:por|durante)\s+\d+\s+(?:días|horas)'
    )
    
    tiempo_match = re.search(patron_tiempo, full_text, re.IGNORECASE)
    if tiempo_match: 
        tiempo = tiempo_match.group(0).strip().capitalize()
        
    # 6. Extraer Permalink (Texto, Historias y Fotos)
    permalink = None
    for a in a_tags:
        href = a['href']
        if any(x in href for x in ['/posts/', 'story.php', 'story_fbid=', '/photo', 'fbid=']):
            if not any(x in href for x in ['l.facebook.com', 'profile.php', 'hashtag']):
                if href.startswith('/'):
                    permalink = f"https://m.facebook.com{href}"
                elif href.startswith('http'):
                    permalink = href
                break

    unique_string = f"{url}_{game}".strip().lower()
    post_id = hashlib.md5(unique_string.encode('utf-8')).hexdigest()

    return {
        "juego": game, "url": url, "plataforma": platform,
        "tiempo": tiempo, "imagen": image_url, "id": post_id,
        "raw_text": full_text.replace('\n', ' '),
        "permalink": permalink
    }

def send_to_discord(post, webhook_url):
    embed = {
        "title": f"🎮 ¡{post['juego']} gratis!",
        "color": 3066993, 
        "fields": [
            {"name": "Plataforma", "value": f"🔹 {post['plataforma']}", "inline": True},
            {"name": "Tiempo", "value": f"⏰ {post['tiempo']}", "inline": True},
            {"name": "Enlace de obtención", "value": post['url'], "inline": False}
        ]
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
        page.goto("https://m.facebook.com/FreeSteamGamesJuegosSteamGratis", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        
        # Gestión de cookies
        botones_cookies = [
            "text='Permitir todas las cookies'", "text='Aceptar todas'",
            "text='Allow all cookies'", "text='De acuerdo'",
            "button:has-text('Permitir')", "button:has-text('Aceptar')"
        ]
        for selector_cookie in botones_cookies:
            boton = page.locator(selector_cookie).first
            if boton.is_visible():
                try:
                    boton.click(timeout=3000)
                    page.wait_for_timeout(2000) 
                    break
                except Exception:
                    pass

        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        
        print("Forzando scroll para cargar imágenes diferidas...")
        for _ in range(6):
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(1500)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        print("Expandiendo textos ocultos (Ver más)...")
        page.evaluate("""
            const buttons = Array.from(document.querySelectorAll('div[role="button"], a'));
            buttons.forEach(btn => {
                const text = btn.innerText || btn.textContent;
                if (text && (text.includes('Ver más') || text.includes('See more'))) {
                    try { btn.click(); } catch(e) {}
                }
            });
        """)
        page.wait_for_timeout(2000) # Esperar a que se despliegue el texto
        # ---------------------------------------------
        
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        posts = soup.find_all('div', attrs={'role': 'article'})
        if len(posts) == 0:
            posts = soup.find_all('div', attrs={'data-tracking-duration-id': True})
            if len(posts) == 0:
                posts = soup.find_all('article')

        print(f"📦 Total de posts estructurales encontrados: {len(posts)}")

        detected_new = False
        processed_count = 0

        for p_element in posts:
            data = parse_post_data(p_element)
            
            if len(data['raw_text']) < 15:
                continue
                
            processed_count += 1
            
            # --- BÚSQUEDA ROBUSTA EN COMENTARIOS ---
            if data['url'] == "No encontrada" and data.get('permalink'):
                print(f"🔍 Link no encontrado en el post. Revisando comentarios de: {data['juego']}...")
                try:
                    comment_page = context.new_page()
                    comment_page.goto(data['permalink'], wait_until="domcontentloaded", timeout=60000)
                    comment_page.wait_for_timeout(2000)
                    
                    comment_page.mouse.wheel(0, 500)
                    comment_page.wait_for_timeout(1500)
                    
                    comment_soup = BeautifulSoup(comment_page.content(), "html.parser")
                    found_url = None

                    # A. Búsqueda por etiquetas <a>
                    for a in comment_soup.find_all('a', href=True):
                        href = a['href']
                        potential = None
                        if "l.facebook.com/l.php" in href:
                            parsed_href = urllib.parse.urlparse(href)
                            query_params = urllib.parse.parse_qs(parsed_href.query)
                            if 'u' in query_params:
                                potential = urllib.parse.unquote(query_params['u'][0])
                        else:
                            potential = href

                        if potential and es_url_valida(potential):
                            found_url = potential
                            break

                    # B. Búsqueda por texto plano si no estaba en <a>
                    if not found_url:
                        full_comment_text = comment_soup.get_text(separator=" ")
                        extracted_urls = re.findall(r'(https?://[^\s]+)', full_comment_text)
                        for u in extracted_urls:
                            u_clean = u.rstrip('.').rstrip(')').rstrip('/')
                            if es_url_valida(u_clean):
                                found_url = u_clean
                                break

                    if found_url:
                        data['url'] = found_url

                    comment_page.close()
                    
                    # Actualizar plataforma e id único
                    if data['url'] != "No encontrada":
                        if "steam" in data['url'].lower(): data['plataforma'] = "STEAM"
                        elif "epic" in data['url'].lower(): data['plataforma'] = "EPIC GAMES"
                        elif "gog" in data['url'].lower(): data['plataforma'] = "GOG"
                        
                        unique_string = f"{data['url']}_{data['juego']}".strip().lower()
                        data['id'] = hashlib.md5(unique_string.encode('utf-8')).hexdigest()
                    
                except Exception as e:
                    print(f"❌ Error al intentar abrir los comentarios: {e}")
                    try:
                        comment_page.close() 
                    except Exception:
                        pass

            if data['url'] == "No encontrada":
                print(f"⚠️ No se encontró link para {data['juego']} (ni en post ni en comentarios). Omitiendo...")
                continue
                
            print(f"\n--- Analizando Post #{processed_count} ---")
            print(f"Juego: {data['juego']}")
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

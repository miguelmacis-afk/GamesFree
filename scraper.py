import os
import json
import re
import requests
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HISTORY_FILE = "history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data if isinstance(data, list) else []
            except: return []
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def parse_post_data(post_element):
    text_elem = post_element.find(attrs={"data-ad-comet-preview": "post_message"})
    
    if not text_elem:
        for div in post_element.find_all('div', dir='auto'):
            div_text = div.get_text().lower()
            if "gratis" in div_text or "steam" in div_text:
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

    # 2. Extraer Imagen del juego
    image_url = None
    img_tags = post_element.find_all('img')
    for img in img_tags:
        src = img.get('src', '')
        if "http" in src and "emoji.php" not in src and "rsrc.php" not in src:
            image_url = src
            break

    # 3. Plataforma
    platform = "OTRA"
    lower_text = full_text.lower()
    if "steam" in url.lower() or "steam" in lower_text: platform = "STEAM"
    elif "epic" in url.lower() or "epic" in lower_text: platform = "EPIC GAMES"
    elif "gog" in url.lower() or "gog" in lower_text: platform = "GOG"

    # 4. Nombre del Juego
    game = full_text.split('\n')[0] if full_text else "No detectado"
    game_match = re.search(r'^(.*?)\s+gratis en', full_text, re.IGNORECASE)
    if game_match: 
        game = game_match.group(1).strip()

    # 5. Tiempo (Regex capaz de tolerar saltos de línea tras expandir el "See more")
    tiempo = "Hasta agotar existencias / No especificado"
    tiempo_match = re.search(r'(tienen hasta el \d+ de \s*\w+|hasta el \d+ de \s*\w+)', full_text, re.IGNORECASE)
    if tiempo_match: 
        tiempo = tiempo_match.group(1).strip().capitalize()

    clean_text_id = re.sub(r'\s+', '', full_text[:80])
    post_id = str(hash(clean_text_id))

    return {
        "juego": game, "url": url, "plataforma": platform,
        "tiempo": tiempo, "imagen": image_url, "id": post_id,
        "raw_text": full_text.replace('\n', ' ')
    }

def send_to_discord(post, webhook_url):
    embed = {
        "title": f"🎮 ¡Nuevo juego gratis detectado!",
        "color": 3066993, 
        "fields": [
            {"name": "Juego", "value": f"**{post['juego']}**", "inline": False},
            {"name": "Plataforma", "value": f"🔹 {post['plataforma']}", "inline": True},
            {"name": "Tiempo", "value": f"⏰ {post['tiempo']}", "inline": True},
            {"name": "Enlace de obtención", "value": post['url'], "inline": False}
        ],
        "footer": {"text": "Facebook Scraper Bot"}
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
        page.goto("https://www.facebook.com/FreeSteamGamesJuegosSteamGratis", wait_until="networkidle")
        
        page.wait_for_timeout(4000)
        page.keyboard.press("Escape") 
        
        # --- MEJORA: Buscar y pulsar todos los botones "See more" / "Ver más" ---
        print("Buscando textos truncados para expandir...")
        try:
            # Selector de Playwright que busca elementos interactivos con esos textos concretos
            see_more_buttons = page.locator("div[role='button']:has-text('See more'), div[role='button']:has-text('Ver más'), text='See more...', text='Ver más...'")
            count = see_more_buttons.count()
            print(f"Botones de expansión detectados: {count}")
            
            for i in range(count):
                try:
                    see_more_buttons.nth(i).click(timeout=2000)
                except:
                    pass # Si alguno no es clicleable o desaparece, saltamos al siguiente
            page.wait_for_timeout(2000)
        except Exception as e:
            print(f"Aviso al expandir texto: {e}")

        page.evaluate("window.scrollTo(0, 1000)")
        page.wait_for_timeout(3000)
        
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        browser.close()

    posts = soup.find_all('div', attrs={'role': 'article'})
    print(f"📦 Total de posts estructurales encontrados: {len(posts)}")

    detected_new = False
    processed_count = 0

    for p in posts:
        data = parse_post_data(p)
        
        if len(data['raw_text']) < 15 or data['url'] == "No encontrada":
            continue
            
        processed_count += 1
        print(f"\n--- Analizando Post #{processed_count} ---")
        print(f"Texto completo extraído: {data['raw_text'][:150]}...")
        print(f"Juego Extracted: {data['juego']}")
        print(f"Tiempo Extracted: {data['tiempo']}")
        print(f"URL Extracted: {data['url']}")

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
            break

    if detected_new:
        save_history(new_history)
        print("\n✅ Historial actualizado en history.json.")
    else:
        print("\nNo se encontraron nuevas ofertas elegibles.")

if __name__ == "__main__":
    main()

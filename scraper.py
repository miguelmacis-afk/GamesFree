import os
import json
import re
import requests
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
    full_text = post_element.get_text(separator="\n").strip()
    
    # 1. Extraer Imagen
    image_url = None
    img_tag = post_element.find('img')
    if img_tag and 'src' in img_tag.attrs and 'http' in img_tag['src']:
        image_url = img_tag['src']

    # 2. Extraer URL
    urls = re.findall(r'(https?://[^\s]+)', full_text)
    url = "No encontrada"
    for u in urls:
        if "facebook.com" not in u: # Priorizar enlaces externos (Steam, Epic, etc)
            url = u.rstrip('.').rstrip('/')
            break
    
    # 3. Plataforma
    platform = "OTRA"
    lower_text = full_text.lower()
    if "steam" in url.lower() or "steam" in lower_text: platform = "STEAM"
    elif "epic" in url.lower() or "epic" in lower_text: platform = "EPIC GAMES"
    elif "gog" in url.lower() or "gog" in lower_text: platform = "GOG"

    # 4. Nombre del Juego
    game = full_text.split('\n')[0]
    game_match = re.search(r'^(.*?)\s+gratis en', full_text, re.IGNORECASE)
    if game_match: game = game_match.group(1).strip()

    # 5. Tiempo
    tiempo = "No especificado"
    tiempo_match = re.search(r'(tienen hasta el \d+ de \w+|hasta el \d+ de \w+)', full_text, re.IGNORECASE)
    if tiempo_match: tiempo = tiempo_match.group(1).capitalize()

    return {
        "juego": game, "url": url, "plataforma": platform,
        "tiempo": tiempo, "imagen": image_url, "id": hash(full_text[:100])
    }

def send_to_discord(post, webhook_url):
    embed = {
        "title": f"🎮 ¡Nuevo juego gratis!",
        "color": 3447003,
        "fields": [
            {"name": "Juego", "value": post['juego'], "inline": False},
            {"name": "Plataforma", "value": post['plataforma'], "inline": True},
            {"name": "Tiempo", "value": post['tiempo'], "inline": True},
            {"name": "Enlace", "value": post['url'], "inline": False}
        ]
    }
    if post['imagen']: embed["image"] = {"url": post['imagen']}
    
    requests.post(webhook_url, json={"embeds": [embed]})

def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK")
    if not webhook_url:
        print("Falta la variable DISCORD_WEBHOOK")
        return

    history = load_history()
    new_history = list(history)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Usar un perfil más realista
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        page = context.new_page()
        
        print("Abriendo Facebook...")
        page.goto("https://www.facebook.com/FreeSteamGamesJuegosSteamGratis", wait_until="networkidle")
        
        # Intentar cerrar el banner de cookies o login si aparece
        try:
            page.wait_for_timeout(3000)
            # Presionar Esc para intentar cerrar diálogos modales de login
            page.keyboard.press("Escape")
        except: pass

        # Scroll suave para cargar imágenes
        page.evaluate("window.scrollTo(0, 1000)")
        page.wait_for_timeout(2000)
        
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        browser.close()

    # Selector alternativo: Facebook a veces usa estos roles para los posts
    posts = soup.find_all('div', attrs={'role': 'article'})
    print(f"Posts encontrados: {len(posts)}")

    detected_new = False
    for p in posts:
        data = parse_post_data(p)
        
        # Ignorar si el texto es muy corto (ruido)
        if len(data['juego']) < 5 or data['url'] == "No encontrada":
            continue

        post_id = str(data['id'])
        if post_id not in history:
            print(f"Nuevo post detectado: {data['juego']}")
            send_to_discord(data, webhook_url)
            new_history.append(post_id)
            detected_new = True
            # Solo procesar los más recientes para evitar spam masivo la primera vez
            if len(new_history) - len(history) > 5: break

    if detected_new:
        save_history(new_history)
        print("Historial actualizado.")
    else:
        print("No hay novedades.")

if __name__ == "__main__":
    main()

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
            try: return json.load(f)
            except: return []
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def parse_post_data(post_element):
    """Extrae texto e imagen de un elemento de post específico."""
    # Extraer todo el texto del post
    full_text = post_element.get_text(separator="\n").strip()
    
    # 1. Extraer Imagen (Buscamos la imagen principal del post)
    image_url = None
    img_tag = post_element.find('img', attrs={'alt': True}) # Facebook suele poner el texto del post en el ALT o etiquetas descriptivas
    if img_tag and 'src' in img_tag.attrs:
        image_url = img_tag['src']

    # 2. Extraer URL de la tienda
    urls = re.findall(r'(https?://[^\s]+)', full_text)
    url = urls[0].rstrip('.') if urls else "No encontrada"
    
    # 3. Determinar Plataforma
    platform = "OTRA"
    if "steam" in url.lower() or "steam" in full_text.lower(): platform = "STEAM"
    elif "epic" in url.lower() or "epic" in full_text.lower(): platform = "EPIC GAMES"
    elif "gog" in url.lower() or "gog" in full_text.lower(): platform = "GOG"

    # 4. Extraer Nombre del Juego
    game = "No detectado"
    game_match = re.search(r'^(.*?)\s+gratis en', full_text, re.IGNORECASE)
    game = game_match.group(1).strip() if game_match else full_text.split('\n')[0]

    # 5. Extraer Tiempo
    tiempo = "Hasta agotar existencias / No especificado"
    tiempo_match = re.search(r'(tienen hasta el \d+ de \w+|hasta el \d+ de \w+)', full_text, re.IGNORECASE)
    if tiempo_match: tiempo = tiempo_match.group(1).capitalize()

    return {
        "juego": game,
        "url": url,
        "plataforma": platform,
        "tiempo": tiempo,
        "imagen": image_url,
        "raw_text": full_text
    }

def send_to_discord(post, webhook_url):
    embed = {
        "title": f"🎮 ¡Nuevo juego gratis detectado!",
        "color": 5814783,
        "fields": [
            {"name": "Juego", "value": f"**{post['juego']}**", "inline": False},
            {"name": "Plataforma", "value": f"🔹 {post['plataforma']}", "inline": True},
            {"name": "Tiempo", "value": f"⏰ {post['tiempo']}", "inline": True},
            {"name": "Enlace", "value": post['url'], "inline": False}
        ],
        "footer": {"text": "Facebook Scraper Bot"}
    }
    
    # Añadir la imagen al embed si existe
    if post['imagen']:
        embed["image"] = {"url": post['imagen']}
    
    payload = {"embeds": [embed]}
    requests.post(webhook_url, json=payload)

def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK")
    if not webhook_url: return

    history = load_history()
    new_history = list(history)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
        page = context.new_page()
        page.goto("https://www.facebook.com/FreeSteamGamesJuegosSteamGratis", wait_until="networkidle")
        page.wait_for_timeout(5000) # Esperar carga de imágenes
        
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        browser.close()

    # Buscamos los contenedores de artículos/posts
    posts = soup.find_all('div', attrs={'role': 'article'})

    detected_new = False
    for p in posts:
        # Usamos el texto para identificar si es nuevo
        text_content = p.get_text().strip()
        if len(text_content) < 20 or "http" not in text_content: continue
        
        post_id = str(hash(text_content))
        if post_id in history: continue

        # Extraer datos incluyendo imagen
        data = parse_post_data(p)
        
        send_to_discord(data, webhook_url)
        new_history.append(post_id)
        detected_new = True

    if detected_new:
        save_history(new_history)

if __name__ == "__main__":
    main()

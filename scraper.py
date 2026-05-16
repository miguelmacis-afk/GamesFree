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
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def parse_post(text):
    # 1. Extraer URL
    urls = re.findall(r'(https?://[^\s]+)', text)
    url = urls[0] if urls else "No encontrada"
    
    # Limpiar posibles caracteres finales de la URL raspada
    url = url.rstrip('.')

    # 2. Extraer Plataforma basada en la URL o Texto
    platform = "OTRA"
    if "steam" in url.lower() or "steam" in text.lower():
        platform = "STEAM"
    elif "epic" in url.lower() or "epic" in text.lower():
        platform = "EPIC GAMES"
    elif "gog" in url.lower() or "gog" in text.lower():
        platform = "GOG"

    # 3. Extraer Nombre del Juego (Texto antes de "gratis en")
    game = "No detectado"
    game_match = re.search(r'^(.*?)\s+gratis en', text, re.IGNORECASE)
    if game_match:
        game = game_match.group(1).strip()
    else:
        # Fallback: Primera línea del post
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if lines:
            game = lines[0]

    # 4. Extraer Tiempo / Límite
    tiempo = "Hasta agotar existencias / No especificado"
    tiempo_match = re.search(r'(tienen hasta el \d+ de \w+|hasta el \d+ de \w+)', text, re.IGNORECASE)
    if tiempo_match:
        tiempo = tiempo_match.group(1).capitalize()

    return {
        "juego": game,
        "url": url,
        "plataforma": platform,
        "tiempo": tiempo,
        "raw_text": text
    }

def send_to_discord(post, webhook_url):
    embed = {
        "title": f"🎮 ¡Nuevo juego gratis detectado!",
        "color": 5814783,  # Color Blurple de Discord
        "fields": [
            {"name": "Juego", "value": f"**{post['juego']}**", "inline": False},
            {"name": "Plataforma", "value": f"🔹 {post['plataforma']}", "inline": True},
            {"name": "Tiempo", "value": f"⏰ {post['tiempo']}", "inline": True},
            {"name": "Enlace de obtención", "value": post['url'], "inline": False}
        ],
        "footer": {
            "text": "Facebook Scraper Bot"
        }
    }
    
    payload = {"embeds": [embed]}
    response = requests.post(webhook_url, json=payload)
    if response.status_code == 204:
        print(f"✅ Notificación enviada para: {post['juego']}")
    else:
        print(f"❌ Error enviando a Discord: {response.status_code}")

def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK")
    if not webhook_url:
        print("❌ Error: La variable de entorno DISCORD_WEBHOOK no está configurada.")
        return

    history = load_history()
    new_history = list(history)

    with sync_playwright() as p:
        # Lanzamos el navegador simulando un usuario común
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print("Navegando a Facebook...")
        page.goto("https://www.facebook.com/FreeSteamGamesJuegosSteamGratis", wait_until="networkidle")
        
        # Esperar un momento a que terminen de cargar los elementos dinámicos
        page.wait_for_timeout(5000)
        
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        browser.close()

    # Selector común en el nuevo diseño de Facebook para el texto de los posts
    post_elements = soup.find_all(attrs={"data-ad-comet-preview": "post_message"})
    
    # Fallback si el selector principal falla (buscar bloques de texto estructurados)
    if not post_elements:
        post_elements = soup.find_all('div', dir='auto')

    detected_new = False

    for elem in post_elements:
        text = elem.get_text(separator="\n").strip()
        
        # Filtro básico para evitar ruido o textos vacíos
        if len(text) < 15 or "http" not in text:
            continue
        
        # Identificador único basado en el texto para el historial
        post_id = hash(text)
        
        if str(post_id) in history:
            continue  # Ya procesado anteriormente

        # Parsear información
        parsed_data = parse_post(text)
        
        # Enviar aviso y registrar en el historial
        send_to_discord(parsed_data, webhook_url)
        new_history.append(str(post_id))
        detected_new = True

    if detected_new:
        save_history(new_history)
    else:
        print("✨ No se encontraron publicaciones nuevas.")

if __name__ == "__main__":
    main()
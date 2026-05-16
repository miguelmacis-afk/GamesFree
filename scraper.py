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
    full_text = post_element.get_text(separator="\n").strip()
    
    # 1. Extraer URL buscando enlaces reales y decodificando el redireccionamiento de Facebook
    url = "No encontrada"
    a_tags = post_element.find_all('a', href=True)
    for a in a_tags:
        href = a['href']
        # Si Facebook camufla el link externo detrás de su redirección l.facebook.com
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

    # Si no se encontró en las etiquetas <a>, intentar por texto plano (como último recurso)
    if url == "No encontrada":
        urls = re.findall(r'(https?://[^\s]+)', full_text)
        for u in urls:
            if "facebook.com" not in u:
                url = u.rstrip('.').rstrip('/')
                break

    # 2. Extraer Imagen (evitando iconos pequeños de emojis o avatares)
    image_url = None
    img_tags = post_element.find_all('img')
    for img in img_tags:
        src = img.get('src', '')
        # Las imágenes de posts suelen ser grandes y no contienen rutas de emojis/fbsbx
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

    # 5. Tiempo
    tiempo = "Hasta agotar existencias / No especificado"
    tiempo_match = re.search(r'(tienen hasta el \d+ de \w+|hasta el \d+ de \w+)', full_text, re.IGNORECASE)
    if tiempo_match: 
        tiempo = tiempo_match.group(1).capitalize()

    # Generamos un ID único limpio basado en los primeros caracteres del texto
    clean_text_id = re.sub(r'\s+', '', full_text[:80])
    post_id = str(hash(clean_text_id))

    return {
        "juego": game, "url": url, "plataforma": platform,
        "tiempo": tiempo, "imagen": image_url, "id": post_id,
        "raw_text": full_text[:120]  # Guardamos un fragmento para los logs
    }

def send_to_discord(post, webhook_url):
    embed = {
        "title": f"🎮 ¡Nuevo juego gratis detectado!",
        "color": 3066993, # Verde llamativo
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
        
        # Esperar y cerrar cualquier modal molesto simulando la tecla Escape
        page.wait_for_timeout(4000)
        page.keyboard.press("Escape")
        
        # Scroll leve para cargar elementos perezosos (lazy-loading de imágenes)
        page.evaluate("window.scrollTo(0, 1000)")
        page.wait_for_timeout(3000)
        
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        browser.close()

    posts = soup.find_all('div', attrs={'role': 'article'})
    print(f"📦 Total de posts estructurales encontrados: {len(posts)}")

    detected_new = False
    processed_count = 0

    for idx, p in enumerate(posts, start=1):
        data = parse_post_data(p)
        
        # Omitir si es un bloque vacío o no tiene texto legible de juego
        if len(data['raw_text']) < 15:
            continue
            
        processed_count += 1
        print(f"\n--- Analizando Post #{processed_count} ---")
        print(f"Texto corto: {data['raw_text']}...")
        print(f"URL encontrada: {data['url']}")
        print(f"Imagen encontrada: {'Sí' if data['imagen'] else 'No'}")

        # Filtro de seguridad: Si no pudimos rescatar un enlace válido, no lo mandamos
        if data['url'] == "No encontrada":
            print("⚠️ Post omitido: No se localizó un enlace externo válido.")
            continue

        post_id = data['id']
        if post_id in history:
            print("🛑 Este juego ya fue procesado anteriormente (está en el historial).")
            continue

        print(f"🚀 ¡Novedad detectada!: Enviando '{data['juego']}' a Discord...")
        status = send_to_discord(data, webhook_url)
        
        if status in [200, 204]:
            new_history.append(post_id)
            detected_new = True
        else:
            print(f"❌ Error al enviar a Discord (Código: {status})")

        # Evitar saturar enviando demasiados posts juntos en la primerísima ejecución
        if len(new_history) - len(history) >= 4:
            print("Limiting initial batch to 4 posts.")
            break

    if detected_new:
        save_history(new_history)
        print("\n✅ Historial guardado y actualizado en history.json.")
    else:
        print("\nFormato analizado. No se encontraron publicaciones nuevas elegibles.")

if __name__ == "__main__":
    main()

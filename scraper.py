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
        
        # --- GESTIÓN ANTIBLOQUEO: VENTANA DE COOKIES DE META ---
        print("Comprobando si aparece el aviso de cookies de Facebook...")
        botones_cookies = [
            "text='Permitir todas las cookies'",
            "text='Aceptar todas'",
            "text='Allow all cookies'",
            "text='De acuerdo'",
            "button:has-text('Permitir')",
            "button:has-text('Aceptar')"
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

        # Scroll inicial para cargar el feed inicial antes de expandir
        print("Realizando scroll inicial para cargar publicaciones...")
        for _ in range(4):
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(1000)
        
        # --- SOLUCIÓN MEJORADA AL CLIC DE "VER MÁS" ---
        print("Buscando y expandiendo textos truncados (Ver más)...")
        # Combinamos textos en español/inglés junto con selectores estructurales típicos de m.facebook
        selectores_ver_mas = [
            "text='Ver más'", 
            "text='See more'", 
            "text='Ver más...'", 
            "text='See more...'",
            "a:has-text('Ver más')",
            "a:has-text('See more')"
        ]
        
        for selector in selectores_ver_mas:
            while True:
                boton = page.locator(selector).filter(has_not_text="Ver más de").first
                if boton.is_visible():
                    try:
                        # Hacemos scroll hasta el botón para que sea clickeable de forma segura
                        boton.scroll_into_view_if_needed(timeout=2000)
                        boton.click(timeout=2000)
                        # Espera crucial para que Facebook inyecte el texto dinámico en el DOM
                        page.wait_for_timeout(800)  
                    except:
                        break
                else:
                    break

        # Scroll final definitivo para asegurar la carga de todas las imágenes mutadas
        print("Forzando scroll final para estabilizar imágenes...")
        for _ in range(4):
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(1000)
            try:
                page.keyboard.press("Escape")
            except:
                pass
        
        page.wait_for_timeout(2000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        browser.close()

    # --- El resto de la lógica de extracción de posts e historial se mantiene igual ---
    posts = soup.find_all('div', attrs={'role': 'article'})
    if len(posts) == 0:
        posts = soup.find_all('div', attrs={'data-tracking-duration-id': True})
        if len(posts) == 0:
            posts = soup.find_all('article')

    print(f"📦 Total de posts estructurales encontrados: {len(posts)}")

    detected_new = False
    processed_count = 0

    for p in posts:
        data = parse_post_data(p)
        
        if len(data['raw_text']) < 15 or data['url'] == "No encontrada":
            continue
            
        processed_count += 1
        print(f"\n--- Analizando Post #{processed_count} ---")
        print(f"Juego: {data['juego']}")
        print(f"Tiempo: {data['tiempo']}")  # Añadido print para verificar el tiempo en consola
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

    if detected_new:
        save_history(new_history)
        print("\n✅ Historial actualizado con éxito en history.json.")
    else:
        print("\nNo se encontraron nuevas ofertas elegibles en esta ejecución.")

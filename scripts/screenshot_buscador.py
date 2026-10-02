"""Capturas de la pestaña Buscador.
Uso: RADAR_TEST_PIN=... .venv/bin/python scripts/screenshot_buscador.py [URL_PANEL] [palabra]
(panel local con API: node scripts/dev_server.js 8766)"""
import asyncio, os, sys
from playwright.async_api import async_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/"
WORD = sys.argv[2] if len(sys.argv) > 2 else "capybara"
PIN = os.environ.get("RADAR_TEST_PIN", "")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        for name, vp, mobile in (("desktop", {"width": 1366, "height": 1000}, False), ("mobile", {"width": 400, "height": 860}, True)):
            ctx = await b.new_context(viewport=vp, is_mobile=mobile, device_scale_factor=2 if mobile else 1)
            if PIN:
                await ctx.add_init_script(f"localStorage.setItem('ttr_pin', {PIN!r})")
            pg = await ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto(URL + "?buscar=" + WORD, wait_until="networkidle")
            await pg.wait_for_selector(".shead, .warnbox", timeout=60000)
            await pg.wait_for_timeout(1500)
            await pg.screenshot(path=f"screenshots/buscador-{WORD}-{name}.png", full_page=False)
            await pg.screenshot(path=f"screenshots/buscador-{WORD}-{name}-full.png", full_page=True)
            print(name, "errores JS:", errs or "ninguno")
            await ctx.close()
        await b.close()
asyncio.run(main())

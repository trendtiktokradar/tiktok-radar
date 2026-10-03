"""Capturas de la pestaña Buscador (gráfica con tooltip y periodo elegido).
Uso: RADAR_TEST_PIN=... .venv/bin/python scripts/screenshot_buscador.py [URL_PANEL] [palabra] [periodo 7|30|90]
(panel local con API: node scripts/dev_server.js 8766)"""
import asyncio, os, sys
from playwright.async_api import async_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/"
WORD = sys.argv[2] if len(sys.argv) > 2 else "capybara"
PERIOD = sys.argv[3] if len(sys.argv) > 3 else "30"
PIN = os.environ.get("RADAR_TEST_PIN", "")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        for name, vp, mobile in (("desktop", {"width": 1366, "height": 1000}, False), ("mobile", {"width": 400, "height": 860}, True)):
            ctx = await b.new_context(viewport=vp, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
            await ctx.add_init_script(f"localStorage.setItem('ttr_speriod', {PERIOD!r});" + (f"localStorage.setItem('ttr_pin', {PIN!r})" if PIN else ""))
            pg = await ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto(URL + "?buscar=" + WORD, wait_until="networkidle")
            await pg.wait_for_selector(".shead, .warnbox", timeout=60000)
            await pg.wait_for_timeout(2500 if PERIOD == "7" else 1200)
            tag = f"{WORD}-{PERIOD}d-{name}"
            await pg.screenshot(path=f"screenshots/buscador-{tag}.png")
            card = await pg.query_selector("#tcard")
            if card:
                await card.scroll_into_view_if_needed()
                hit = await pg.query_selector("#thit")
                bb = await hit.bounding_box()
                x, y = bb["x"] + bb["width"] * 0.82, bb["y"] + bb["height"] * 0.5
                if mobile:
                    await pg.touchscreen.tap(x, y)
                else:
                    await pg.mouse.move(x, y)
                await pg.wait_for_timeout(300)
                await card.screenshot(path=f"screenshots/buscador-grafica-{tag}.png")
                v = await pg.query_selector("#tcard + .scard")
                if v:
                    await v.screenshot(path=f"screenshots/buscador-viral-{WORD}-{name}.png")
            print(name, "errores JS:", errs or "ninguno")
            await ctx.close()
        await b.close()
asyncio.run(main())

"""Captura del panel servido en local (python3 -m http.server 8765 -d web)."""
import asyncio, sys, time
from playwright.async_api import async_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/"
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        prev = int((time.time() - 90) * 1000)  # simula "última visita" hace 90 s para ver el badge NUEVA
        for name, vp, mobile in (("desktop", {"width": 1366, "height": 1000}, False), ("mobile", {"width": 400, "height": 860}, True)):
            ctx = await b.new_context(viewport=vp, is_mobile=mobile, device_scale_factor=2 if mobile else 1)
            await ctx.add_init_script(f"if(!sessionStorage.getItem('x')){{localStorage.setItem('ttr_last_visit','{prev}');sessionStorage.setItem('x','1')}}")
            pg = await ctx.new_page()
            await pg.goto(URL, wait_until="networkidle"); await pg.wait_for_timeout(1500)
            await pg.screenshot(path=f"screenshots/panel-{name}.png", full_page=False)
            await pg.click("nav button[data-tab=trends]"); await pg.wait_for_timeout(500)
            await pg.screenshot(path=f"screenshots/trends-{name}.png")
            await pg.click("nav button[data-tab=sources]"); await pg.wait_for_timeout(300)
            await pg.screenshot(path=f"screenshots/sources-{name}.png")
            errs = []
            await ctx.close()
        await b.close()
asyncio.run(main())

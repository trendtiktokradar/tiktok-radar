#!/usr/bin/env python3
"""Buscador de TikTok Radar: servicio en el box (sin IA, sin login de TikTok).

Qué hace (bajo demanda, cuando Alex busca una palabra en la pestaña "Buscador"):
  1. TikTok (Chrome headless propio, sin login): detalle del hashtag (#palabra → nº de vídeos y views totales),
     2-3 páginas de vídeos del hashtag y 1-2 páginas de la búsqueda normal de TikTok. TikTok firma las peticiones
     (X-Bogus/X-Gnarly) con su propio JS, así que se piden con fetch() DESDE la página de tiktok.com abierta.
  2. Google Trends (pytrends): curva de 90 días en búsquedas web y de YouTube → sube / baja / estable.
  3. tikwm.com (no oficial): hashtags relacionados con nº de vídeos (y respaldo si TikTok no da el detalle).
  4. Veredicto 🔥 hot / sube / baja / flojo con reglas fijas (ver README, sección Buscador).
Caché 45 min por palabra, 1 búsqueda nueva cada 4 s como mucho, resultados parciales si una fuente falla.
Watchlist opcional: las palabras que Alex sigue guardan una foto diaria (vídeos/views) → curva de crecimiento real.

Se expone a internet con un túnel gratis de Pinggy (ssh sobre TLS por el 443, sin cuenta; el box bloquea el 7844 de cloudflared). La URL cambia
al reiniciar y cada ~50 min (los túneles gratis caducan a los 60), el servicio publica la URL actual en box.json de la rama "feedback" del repo; la función de Vercel
/api/search la lee de ahí (el navegador nunca ve la URL del box). Cada petición lleva el PIN del panel, que el
servicio comprueba preguntando a /api/search (action "check") de la web: el box no guarda el PIN.

Arrancar / parar: scripts/buscador.sh start|stop|status|ensure   (log: logs/buscador.log)
"""
import asyncio, hashlib, hmac, json, logging, math, os, re, shutil, signal, statistics, sys, time, urllib.parse
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("BUSCADOR_PORT", "18790"))
PROFILE = os.path.expanduser(os.environ.get("BUSCADOR_PROFILE", "~/.tiktok-radar-chrome"))
CHROME = os.environ.get("BUSCADOR_CHROME", "/usr/bin/google-chrome")
STATE_FILE = Path(os.environ.get("BUSCADOR_STATE", ROOT / "state" / "buscador.json"))
WEB_URL = os.environ.get("RADAR_WEB_URL", "https://tiktok-radar-web.vercel.app").rstrip("/")
REPO = os.environ.get("RADAR_REPO", "trendtiktokradar/tiktok-radar")
BOX_BRANCH = os.environ.get("RADAR_FEEDBACK_BRANCH", "feedback")
BOX_FILE = "box.json"
TOKEN_VAR = os.environ.get("RADAR_TOKEN_VAR", "GITHUB_TOKEN_TIKTOK_RADAR")
TUNNEL = os.environ.get("BUSCADOR_TUNNEL", "1") == "1"     # 0 = solo local (pruebas)
PUBLISH = os.environ.get("BUSCADOR_PUBLISH", "1") == "1"   # 0 = no escribir box.json en GitHub
CACHE_TTL = 45 * 60
TRENDS_TTL = 6 * 3600
MIN_GAP = 4.0            # segundos mínimos entre búsquedas nuevas (no cacheadas)
MAX_QUEUE = 3
LATEST_N = 20            # "Últimos vídeos": los más nuevos de la muestra, por fecha
SEARCH_BUDGET = 45       # segundos máximos por búsqueda
HASHTAG_PAGES = 6       # 6 × 30 vídeos del hashtag (páginas 2-6 en paralelo)
SEARCH_PAGES = 4        # 4 × ~12 de la búsqueda (páginas 2-4 en paralelo)
VIRAL_VIEWS = int(os.environ.get("BUSCADOR_VIRAL_VIEWS", "100000"))   # umbral de "vídeo viral"
TZ = os.environ.get("BUSCADOR_TZ", "Europe/Madrid")
MAX_WATCH = 25
SNAPSHOT_EVERY = 23 * 3600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
VERSION = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%F %T")
log = logging.getLogger("buscador")
now_s = lambda: int(time.time())


def norm_q(q):
    q = re.sub(r"\s+", " ", str(q or "")).strip().lstrip("#").strip()
    return q[:60]


def to_tag(q):
    return re.sub(r"[^0-9a-z_\u00c0-\uffff]", "", q.lower())[:50]


def fnum(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- TikTok (Chrome headless, sin login)
class TikTok:
    JS = """async ([u, ms]) => { const c = new AbortController(); const t = setTimeout(() => c.abort(), ms);
      try { const r = await fetch(u, {credentials: 'include', signal: c.signal}); return [r.status, await r.text()]; }
      catch (e) { return [0, String(e)]; } finally { clearTimeout(t); } }"""
    BASE = "aid=1988&app_name=tiktok_web&device_platform=web_pc"

    def __init__(self):
        self.pw = self.ctx = self.page = None
        self.template = None       # parámetros reales de item_list capturados de la propia web
        self.warmed = 0
        self.lock = asyncio.Lock()
        self.fails = 0             # fallos SEGUIDOS de TikTok (vuelve a 0 con cada respuesta buena)
        self.last_ok = 0.0
        self.last_err = ""
        self.restarts = 0
        self.restarted_at = 0.0

    async def start(self):
        from playwright.async_api import async_playwright
        if self.pw is None:
            self.pw = await async_playwright().start()
        os.makedirs(PROFILE, mode=0o700, exist_ok=True)
        self.ctx = await self.pw.chromium.launch_persistent_context(
            PROFILE, executable_path=CHROME, headless=True, locale="en-US", user_agent=UA,
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--mute-audio"])
        # sin imágenes/vídeos/fuentes: menos ancho de banda, la firma solo necesita el JS
        await self.ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        self.page.on("request", self._on_request)
        await self.warm()

    def _on_request(self, req):
        if "/api/challenge/item_list/" in req.url:
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(req.url).query))
            for k in ("X-Bogus", "X-Gnarly", "X-Dynosaur", "msToken"):
                q.pop(k, None)
            self.template = q

    async def warm(self):
        old, self.template = self.template, None     # plantilla nueva en cada calentamiento (la vieja solo de respaldo)
        await self.page.goto("https://www.tiktok.com/tag/capybara", wait_until="domcontentloaded", timeout=45000)
        for _ in range(30):
            if self.template:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(1.5)
        self.warmed = time.time()
        log.info("TikTok listo (plantilla item_list: %s)", "sí" if self.template else ("la anterior" if old else "no"))
        if not self.template:
            self.template = old

    async def restart(self, fresh=None):
        """Chrome nuevo. fresh=True (o automático si ya se reinició hace < 15 min sin arreglarse): además se borra el
        perfil, porque TikTok puede marcar el perfil (id de dispositivo en localStorage) y seguir dando 403/200 vacíos
        aunque se borren las cookies. Sin sesión iniciada no se pierde nada."""
        if fresh is None:
            fresh = time.time() - self.restarted_at < 15 * 60
        log.warning("reiniciando Chrome%s", " con perfil nuevo" if fresh else "")
        self.restarts += 1
        self.restarted_at = time.time()
        self.template = None
        self.fails = 0
        try:
            if self.ctx:
                await self.ctx.close()
        except Exception:
            pass
        self.ctx = self.page = None
        if fresh:
            shutil.rmtree(PROFILE, ignore_errors=True)
        await self.start()
        if not self.template and not fresh:
            log.warning("sin plantilla tras reiniciar: perfil nuevo")
            await self.restart(fresh=True)

    async def ensure(self):
        async with self.lock:
            try:
                if self.page is None or self.page.is_closed():
                    await self.restart()
                elif self.fails >= 3:
                    # recargar la página no basta cuando TikTok empieza a dar 200 vacíos: Chrome nuevo
                    log.warning("TikTok: %s fallos seguidos (%s)", self.fails, self.last_err)
                    await self.restart()
                elif not self.template and time.time() - self.restarted_at > 5 * 60:
                    # sin plantilla de item_list = la web de TikTok no cargó bien (bloqueo/captcha): Chrome nuevo
                    log.warning("TikTok: sin plantilla item_list, reinicio Chrome")
                    await self.restart()
                elif time.time() - self.warmed > 30 * 60:
                    await self.warm()
            except Exception as e:
                log.warning("ensure falló (%s), reinicio completo", e)
                await self.restart()

    async def get(self, path, timeout=12):
        try:
            st, txt = await asyncio.wait_for(self.page.evaluate(self.JS, [path, timeout * 1000]), timeout + 3)
        except Exception as e:
            self._bad("tiktok: " + type(e).__name__)
        if st != 200 or not txt:
            self._bad(f"tiktok HTTP {st} vacío" if st == 200 else f"tiktok HTTP {st}")
        try:
            d = json.loads(txt)
        except ValueError:
            self._bad("tiktok: respuesta no JSON")
        self.fails, self.last_ok = 0, time.time()
        return d

    def _bad(self, msg):
        self.fails += 1
        self.last_err = msg
        raise RuntimeError(msg)

    async def detail(self, tag):
        d = await self.get(f"/api/challenge/detail/?{self.BASE}&challengeName={urllib.parse.quote(tag)}")
        ci = d.get("challengeInfo") or {}
        ch = ci.get("challenge") or {}
        if not ch.get("id"):
            return {"found": False, "tag": tag, "status": d.get("statusCode")}
        s2, s1 = ci.get("statsV2") or {}, ci.get("stats") or {}
        return {"found": True, "tag": ch.get("title") or tag, "id": ch["id"], "desc": (ch.get("desc") or "")[:200],
                "videos": fnum(s2.get("videoCount")) or fnum(s1.get("videoCount")) or 0,
                "views": fnum(s2.get("viewCount")) or fnum(s1.get("viewCount")) or 0,
                "url": "https://www.tiktok.com/tag/" + urllib.parse.quote(ch.get("title") or tag)}

    async def hashtag_items(self, cid, pages):
        """Página 1 y, si hay más, las páginas 2..N a la vez (el cursor de TikTok es 30, 60, 90…)."""
        if not self.template:
            raise RuntimeError("sin plantilla item_list")
        page = lambda cur: self.get("/api/challenge/item_list/?" + urllib.parse.urlencode(
            dict(self.template, challengeID=str(cid), cursor=str(cur), count="30")))
        d = await page(0)
        out = list(d.get("itemList") or [])
        if d.get("hasMore") and pages > 1:
            step = int(d.get("cursor") or 30) or 30
            rs = await asyncio.gather(*[page(step * k) for k in range(1, pages)], return_exceptions=True)
            for r in rs:
                if isinstance(r, dict):
                    out += r.get("itemList") or []
        return out

    async def search_items(self, kw, pages):
        """Página 1 de la búsqueda y luego las siguientes a la vez (offset 12, 24, 36… con el mismo search_id)."""
        base = f"/api/search/general/full/?{self.BASE}&keyword={urllib.parse.quote(kw)}"
        items = lambda d: [x["item"] for x in d.get("data") or [] if x.get("type") == 1 and x.get("item")]
        d = await self.get(base + "&offset=0")
        out = items(d)
        sid = (d.get("log_pb") or {}).get("impr_id")
        if d.get("has_more") and pages > 1:
            step = int(d.get("cursor") or 12) or 12
            rs = await asyncio.gather(*[self.get(base + f"&offset={step * k}" + (f"&search_id={sid}" if sid else ""))
                                        for k in range(1, pages)], return_exceptions=True)
            for r in rs:
                if isinstance(r, dict):
                    out += items(r)
        return out


def video(it, src):
    st = it.get("statsV2") or it.get("stats") or {}
    st1 = it.get("stats") or {}
    g = lambda k: fnum(st.get(k)) if fnum(st.get(k)) is not None else fnum(st1.get(k))
    au = (it.get("author") or {}).get("uniqueId") or ""
    tags = [x.get("hashtagName") for x in it.get("textExtra") or [] if x.get("hashtagName")]
    if not tags:
        tags = re.findall(r"#(\w+)", it.get("desc") or "")
    vid = str(it.get("id") or "")
    return {"id": vid, "t": fnum(it.get("createTime")) or 0, "desc": (it.get("desc") or "")[:160],
            "views": g("playCount") or 0, "likes": g("diggCount") or 0, "comments": g("commentCount") or 0,
            "shares": g("shareCount") or 0, "author": au,
            "url": f"https://www.tiktok.com/@{au}/video/{vid}" if au else f"https://www.tiktok.com/video/{vid}",
            "cover": (it.get("video") or {}).get("cover") or "", "tags": [t.lower() for t in tags][:15], "src": src}


# ---------------------------------------------------------------- Google Trends + tikwm
def trends_sync(word, period=90):
    """Google Trends web y YouTube (0-100). period 90 → diario (sirve también para 30 días, recortado en la web);
    period 7 → por horas ("now 7-d"), que se agrupa en días (media) y se reescala a 0-100.
    Lento-ish (~2-4 s) y con límite de Google: se cachea 6 h por palabra y periodo."""
    from pytrends.request import TrendReq
    out, errs = {}, []
    pt = TrendReq(hl="es-ES", tz=-120, timeout=(5, 12), retries=0)
    for name, gprop in (("web", ""), ("youtube", "youtube")):
        try:
            pt.build_payload([word], timeframe="now 7-d" if period == 7 else "today 3-m", gprop=gprop)
            df = pt.interest_over_time()
            if df is None or df.empty or word not in df:
                out[name] = {"points": [], "days": [], "direction": "sin datos"}
                continue
            pts = [[int(ts.timestamp()), int(v)] for ts, v in zip(df.index, df[word].tolist())]
            byday = {}
            for ts, v in zip(df.index, df[word].tolist()):
                byday.setdefault(ts.strftime("%Y-%m-%d"), []).append(float(v))
            days = [[d, sum(vs) / len(vs)] for d, vs in sorted(byday.items())]
            if period == 7:      # media diaria en la escala de Google (100 = la hora pico de la semana)
                days = [[d, round(v, 1)] for d, v in days]
            else:
                mx = max([v for _, v in days] + [0])
                days = [[d, round(v * 100 / mx, 1) if mx else 0] for d, v in days]
            if period == 7:
                out[name] = {"points": pts, "days": days, "hourly": True, "direction": "sin datos"}
                continue
            out[name] = {"points": pts, "days": days, **trend_direction([v for _, v in pts])}
        except Exception as e:
            errs.append(f"google trends {name}: {type(e).__name__}")
            out[name] = {"points": [], "days": [], "direction": "sin datos", "error": type(e).__name__}
    return out, errs


def trend_direction(vals):
    """Media últimos 7 días vs media de los 28 días anteriores."""
    if len(vals) < 21 or max(vals) == 0:
        return {"direction": "sin datos", "ratio": None}
    last, prev = vals[-7:], vals[-35:-7]
    a, b = sum(last) / len(last), sum(prev) / max(1, len(prev))
    ratio = round(a / b, 2) if b > 0 else (9.99 if a > 0 else None)
    if max(a, b) < 5:   # casi nadie lo busca en Google: el ratio sería ruido
        return {"direction": "poco volumen", "ratio": None, "last7": round(a, 1), "prev28": round(b, 1)}
    if ratio is None:
        d = "sin datos"
    elif ratio >= 2:
        d = "sube fuerte"
    elif ratio >= 1.3:
        d = "sube"
    elif ratio <= 0.77:
        d = "baja"
    else:
        d = "estable"
    return {"direction": d, "ratio": ratio, "last7": round(a, 1), "prev28": round(b, 1)}


async def tikwm_related(http, kw):
    url = "https://www.tikwm.com/api/challenge/search?" + urllib.parse.urlencode({"keywords": kw, "count": 12})
    async with http.get(url, headers={"User-Agent": UA}, timeout=ClientTimeout(total=10)) as r:
        if r.status != 200:
            raise RuntimeError(f"tikwm HTTP {r.status}")
        d = await r.json(content_type=None)
    lst = ((d or {}).get("data") or {}).get("challenge_list") or []
    return [{"tag": x.get("cha_name"), "videos": fnum(x.get("user_count")) or 0, "views": fnum(x.get("view_count")) or 0,
             "id": str(x.get("id") or "")} for x in lst if x.get("cha_name")]


# ---------------------------------------------------------------- análisis + veredicto
def relevant(v, q, tag):
    text = (v["desc"] + " " + " ".join("#" + t for t in v["tags"])).lower()
    flat = re.sub(r"[^0-9a-z\u00c0-\uffff]", "", text)
    words = [w for w in re.split(r"\s+", q.lower()) if w]
    return bool(tag and tag in flat) or (words and all(w in text for w in words))


def day_of(t):
    from zoneinfo import ZoneInfo
    import datetime as dt
    return dt.datetime.fromtimestamp(t, ZoneInfo(TZ)).strftime("%Y-%m-%d")


def tiktok_days(vids, now, days=90):
    """Por día (hora de Madrid): nº de vídeos de la muestra publicados ese día y suma de sus views."""
    out = {}
    for v in vids:
        if v["t"] and now - v["t"] <= days * 86400 + 86400:
            d = out.setdefault(day_of(v["t"]), [0, 0])
            d[0] += 1
            d[1] += v["views"]
    return [[d, n, vw] for d, (n, vw) in sorted(out.items())]


def first_viral(vids, now, threshold=VIRAL_VIEWS):
    """El vídeo MÁS ANTIGUO de la muestra que pasa el umbral de views. Si ninguno, el más visto."""
    vids = [v for v in vids if v["t"]]
    if not vids:
        return None
    over = [v for v in vids if v["views"] >= threshold]
    if over:
        v, fb = min(over, key=lambda x: x["t"]), False
    else:
        v, fb = max(vids, key=lambda x: (x["views"], -x["t"])), True
    return {"video": v, "threshold": threshold, "fallback": fb, "days_since": round((now - v["t"]) / 86400, 1),
            "day": day_of(v["t"]), "count_over": len(over)}


def rise_start(vids, now):
    """¿Desde qué día sube el volumen? Suma móvil de 7 días de vídeos/día de la muestra (últimos 180 días).
    Base = mediana de esa suma entre 120 y 35 días atrás. Si la suma de hoy o de ayer supera
    max(5, 2 × base), se va hacia atrás mientras siga por encima: el primer día con vídeos de ese tramo = inicio de la subida."""
    import datetime as dt
    cnt = {}
    for v in vids:
        if v["t"] and now - v["t"] <= 180 * 86400:
            k = day_of(v["t"])
            cnt[k] = cnt.get(k, 0) + 1
    if sum(cnt.values()) < 5:
        return {"rising": False, "reason": "pocos vídeos recientes en la muestra"}
    today = dt.date.fromisoformat(day_of(now))
    days = [(today - dt.timedelta(days=i)).isoformat() for i in range(186, -1, -1)]   # antiguo → hoy
    c = [cnt.get(d, 0) for d in days]
    S = [sum(c[max(0, i - 6):i + 1]) for i in range(len(c))]
    n = len(days)
    base_vals = S[n - 121:n - 35]
    base = statistics.median(base_vals) if base_vals else 0
    thr = max(5, 2 * base)
    if not (S[-1] > thr or S[-2] > thr):
        return {"rising": False, "base7": base, "now7": S[-1], "threshold7": thr}
    i = n - 1 if S[-1] > thr else n - 2
    while i > 0 and S[i - 1] > thr:
        i -= 1
    j = next((k for k in range(max(0, i - 6), i + 1) if c[k] > 0), i)
    return {"rising": True, "day": days[j], "days_since": (today - dt.date.fromisoformat(days[j])).days,
            "base7": base, "now7": S[-1], "threshold7": thr}


def analyse(vids, now):
    vids = list({v["id"]: v for v in vids if v["id"]}.values())
    ages = [(now - v["t"]) / 86400 for v in vids if v["t"]]
    b = {"h24": 0, "d7": 0, "d30": 0, "y1": 0, "old": 0}
    for a in ages:
        b["h24" if a <= 1 else "d7" if a <= 7 else "d30" if a <= 30 else "y1" if a <= 365 else "old"] += 1
    views = [v["views"] for v in vids]
    likes = [v["likes"] for v in vids]
    recent = [v for v in vids if v["t"] and now - v["t"] <= 7 * 86400]
    med = lambda xs: int(statistics.median(xs)) if xs else 0
    top = sorted(vids, key=lambda v: v["views"], reverse=True)[:10]
    top_recent = sorted(recent, key=lambda v: v["views"], reverse=True)[:6]
    latest = sorted([v for v in vids if v["t"]], key=lambda v: v["t"], reverse=True)[:LATEST_N]
    return {"n": len(vids), "buckets": b,
            "last24h": b["h24"], "last7d": b["h24"] + b["d7"], "last30d": b["h24"] + b["d7"] + b["d30"],
            "views_median": med(views), "views_max": max(views) if views else 0,
            "likes_median": med(likes), "likes_max": max(likes) if likes else 0,
            "recent_views_max": max([v["views"] for v in recent], default=0),
            "median_age_days": round(statistics.median(ages), 1) if ages else None,
            "top": top, "top_recent": top_recent, "latest": latest}


def verdict(res):
    """Reglas fijas (sin IA). Ver README → Buscador → Veredicto."""
    s, why = 0, []
    a = res.get("sample") or {}
    n = a.get("n") or 0
    if n >= 10:
        share7 = a["last7d"] / n
        if share7 >= 0.25:
            s += 2; why.append(f"+2: {round(share7*100)}% de los vídeos de la muestra son de los últimos 7 días")
        elif share7 >= 0.10:
            s += 1; why.append(f"+1: {round(share7*100)}% de la muestra es de los últimos 7 días")
        else:
            why.append(f"0: solo {round(share7*100)}% de la muestra es de los últimos 7 días")
        if a["last24h"] >= 3:
            s += 1; why.append(f"+1: {a['last24h']} vídeos de las últimas 24 h en la muestra")
    rv = a.get("recent_views_max") or 0
    if rv >= 1_000_000:
        s += 2; why.append(f"+2: un vídeo de esta semana tiene {rv:,} views".replace(",", "."))
    elif rv >= 100_000:
        s += 1; why.append(f"+1: un vídeo de esta semana tiene {rv:,} views".replace(",", "."))
    tw = ((res.get("trends") or {}).get("web") or {})
    ty = ((res.get("trends") or {}).get("youtube") or {})
    ratios = [t.get("ratio") for t in (tw, ty) if t.get("ratio") is not None]
    gdir = "sin datos"
    if ratios:
        r = max(ratios)
        if r >= 2:
            s += 2; gdir = "sube"; why.append(f"+2: Google Trends ×{r} (últimos 7 días vs 4 semanas antes)")
        elif r >= 1.3:
            s += 1; gdir = "sube"; why.append(f"+1: Google Trends ×{r}")
        elif max(ratios) <= 0.77:
            s -= 1; gdir = "baja"; why.append(f"−1: Google Trends baja (×{r})")
        else:
            gdir = "estable"; why.append(f"0: Google Trends estable (×{r})")
    g = res.get("growth") or {}
    if g.get("videos_pct_day") is not None:
        if g["videos_pct_day"] >= 2:
            s += 1; why.append(f"+1: el hashtag crece {g['videos_pct_day']}% de vídeos al día (watchlist)")
    share7 = (a["last7d"] / n) if n else 0
    if (res.get("tiktok") or {}).get("status") == "fallo":
        why.insert(0, "TikTok no ha respondido bien en esta búsqueda: sin sus vídeos el veredicto no es fiable")
        return {"verdict": "sin_datos", "label": "⚠️ sin TikTok", "score": s, "why": why}
    if n < 5 and not ratios and not (res.get("hashtag") or {}).get("found"):
        v, label = "sin_datos", "❔ sin datos"
    elif s >= 5:
        v, label = "hot", "🔥 HOT"
    elif s >= 3:
        v, label = "sube", "📈 sube"
    elif gdir == "baja" or (n >= 10 and share7 < 0.10 and gdir != "sube"):
        v, label = "baja", "📉 baja"
    else:
        v, label = "flojo", "💤 flojo"
    return {"verdict": v, "label": label, "score": s, "why": why}


# ---------------------------------------------------------------- estado (watchlist) en disco
class Store:
    def __init__(self, path):
        self.path = path
        try:
            self.d = json.loads(path.read_text())
        except Exception:
            self.d = {}
        self.d.setdefault("watch", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False))
        tmp.replace(self.path)

    def watch_key(self, q):
        return norm_q(q).lower()

    def snap(self, q, res):
        w = self.d["watch"].get(self.watch_key(q))
        if not w:
            return False
        last = w["snaps"][-1]["t"] if w["snaps"] else 0
        if now_s() - last < SNAPSHOT_EVERY:
            return False
        h = res.get("hashtag") or {}
        a = res.get("sample") or {}
        w["snaps"].append({"t": now_s(), "videos": h.get("videos"), "views": h.get("views"),
                           "last7d": a.get("last7d"), "n": a.get("n"), "verdict": (res.get("verdict") or {}).get("verdict")})
        w["snaps"] = w["snaps"][-120:]
        self.save()
        return True

    def growth(self, q):
        w = self.d["watch"].get(self.watch_key(q))
        if not w:
            return None
        sn = [s for s in w["snaps"] if s.get("videos")]
        out = {"watching": True, "since": w["added"], "snaps": w["snaps"][-90:]}
        if len(sn) >= 2:
            a, b = sn[-2], sn[-1]
            days = max((b["t"] - a["t"]) / 86400, 0.1)
            out["videos_pct_day"] = round(((b["videos"] / a["videos"]) - 1) * 100 / days, 2) if a["videos"] else None
            out["views_pct_day"] = round(((b["views"] / a["views"]) - 1) * 100 / days, 2) if a.get("views") else None
        return out


# ---------------------------------------------------------------- túnel + publicación de la URL
class Tunnel:
    """Túnel público hacia 127.0.0.1:PORT con Pinggy (gratis, sin cuenta): SSH envuelto en TLS por el puerto 443
    (el box solo deja salir 443; cloudflared/localtunnel/SSH normal no pasan). Los túneles gratis de Pinggy caducan
    a los 60 min: a los ROTATE_S se levanta un túnel nuevo, se publica su URL y el viejo se cierra tras un margen.
    El watchdog (tunnel_task) comprueba cada ~30 s que la URL pública responde y, si no, la rehace con espera
    creciente (30 s → 10 min) para no hacer spam de commits ni de conexiones."""
    RX = re.compile(r"https://[a-z0-9-]+\.free\.pinggy\.net")
    ROTATE_S = int(os.environ.get("BUSCADOR_ROTATE_S", "3000"))   # 50 min
    GRACE_S = 90

    def __init__(self):
        self.proc = None
        self.url = None
        self.published = None
        self.since = 0
        self.bad = 0
        self.fails = 0          # arranques fallidos seguidos (para la espera creciente)
        self.next_try = 0.0
        self.old = []           # (proc, cierra_en) túneles viejos que siguen vivos durante el margen

    def _cmd(self):
        return ["ssh", "-T", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                "-o", "NumberOfPasswordPrompts=1", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes",
                "-o", "ProxyCommand=openssl s_client -quiet -verify_quiet -connect a.pinggy.io:443 -servername a.pinggy.io 2>/dev/null",
                "-p", "443", f"-R0:127.0.0.1:{PORT}", "a.pinggy.io"]

    async def _spawn(self):
        """Lanza un ssh nuevo y devuelve (proc, url) o (None, None)."""
        logf = open(ROOT / "logs" / "tunnel.log", "ab")
        proc = await asyncio.create_subprocess_exec(*self._cmd(), stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=logf, start_new_session=True,
                                                    env=dict(os.environ, SSH_ASKPASS=str(ROOT / "scripts" / "empty_askpass.sh"), SSH_ASKPASS_REQUIRE="force", DISPLAY=os.environ.get("DISPLAY", ":0")))
        deadline = time.time() + 45
        url = None
        while time.time() < deadline and proc.returncode is None:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), 5)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            m = self.RX.search(line.decode("utf8", "ignore"))
            if m:
                url = m.group(0)
                break
        if not url:
            await self._kill(proc)
            return None, None
        asyncio.get_running_loop().create_task(self._drain(proc))
        return proc, url

    async def _drain(self, proc):
        try:
            while proc.returncode is None:
                if not await proc.stdout.readline():
                    break
        except Exception:
            pass

    @staticmethod
    async def _kill(proc):
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, 15)
            except Exception:
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, 9)
                except Exception:
                    proc.kill()

    async def start(self, http=None):
        """Levanta un túnel nuevo. El viejo (si lo hay) queda vivo GRACE_S s para que la web tenga tiempo de leer la URL nueva."""
        proc, url = await self._spawn()
        if not url:
            self.fails += 1
            self.next_try = time.time() + min(30 * 2 ** (self.fails - 1), 600)
            log.warning("túnel: no ha arrancado (intento %s); reintento en %ss", self.fails, int(self.next_try - time.time()))
            return None
        if self.proc is not None:
            self.old.append((self.proc, time.time() + self.GRACE_S))
        self.proc, self.url, self.since = proc, url, int(time.time() * 1000)
        self.fails, self.bad, self.next_try = 0, 0, 0.0
        self.born = time.time()
        log.info("túnel activo: %s", url)
        return url

    async def reap(self):
        keep = []
        for p, t in self.old:
            if time.time() >= t or p.returncode is not None:
                await self._kill(p)
            else:
                keep.append((p, t))
        self.old = keep

    async def stop(self):
        await self._kill(self.proc)
        self.proc = None
        for p, _ in self.old:
            await self._kill(p)
        self.old = []

    async def publish(self, http):
        """Escribe box.json en la rama feedback (API Contents de GitHub). Sin token → no publica."""
        tok = os.environ.get(TOKEN_VAR)
        if not (PUBLISH and tok and self.url):
            return False
        h = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tiktok-radar-buscador"}
        api = f"https://api.github.com/repos/{REPO}/contents/{BOX_FILE}"
        doc = {"v": 1, "service": "buscador", "url": self.url, "since": self.since, "version": VERSION}
        import base64
        for _ in range(4):
            sha = None
            async with http.get(api + "?ref=" + BOX_BRANCH, headers=h) as r:
                if r.status == 200:
                    j = await r.json()
                    sha = j.get("sha")
                    try:
                        if json.loads(base64.b64decode(j.get("content") or "")).get("url") == self.url:
                            self.published = self.url
                            return True
                    except Exception:
                        pass
                elif r.status != 404:
                    log.warning("GitHub GET box.json HTTP %s", r.status)
            body = {"message": "Buscador: URL del box actualizada", "branch": BOX_BRANCH,
                    "content": base64.b64encode((json.dumps(doc, indent=1) + "\n").encode()).decode(),
                    "committer": {"name": "tiktok-radar-bot", "email": "tiktok-radar-bot@users.noreply.github.com"}}
            if sha:
                body["sha"] = sha
            async with http.put(api, headers=h, json=body) as r:
                if r.status in (200, 201):
                    self.published = self.url
                    log.info("box.json publicado en la rama %s", BOX_BRANCH)
                    return True
                log.warning("GitHub PUT box.json HTTP %s", r.status)
            await asyncio.sleep(2)
        return False


# ---------------------------------------------------------------- servicio
class Service:
    def __init__(self):
        self.tt = TikTok()
        self.store = Store(STATE_FILE)
        self.tunnel = Tunnel()
        self.cache, self.tcache = {}, {}
        self.search_lock = asyncio.Lock()
        self.last_search = 0.0
        self.waiting = 0
        self.good_pins = {}          # sha256(pin) -> caduca (solo en memoria)
        self.bad_pins = []           # timestamps de PIN incorrectos
        self.http = None
        self.started = time.time()
        self.last_user = 0.0

    # ---- PIN: se valida contra la web (/api/search action=check); el box no guarda el PIN
    async def pin_ok(self, pin):
        if not pin or len(pin) > 100:
            return "bad_pin"
        local = os.environ.get("BUSCADOR_LOCAL_PIN")
        if local and hmac.compare_digest(local.encode(), pin.encode()):
            return "ok"
        h = hashlib.sha256(pin.encode()).hexdigest()
        if self.good_pins.get(h, 0) > time.time():
            return "ok"
        self.bad_pins = [t for t in self.bad_pins if time.time() - t < 3600]
        if len(self.bad_pins) >= 10:
            return "locked"
        try:
            async with self.http.post(WEB_URL + "/api/search", json={"action": "check", "pin": pin},
                                      timeout=ClientTimeout(total=12)) as r:
                if r.status == 200:
                    self.good_pins[h] = time.time() + 3600
                    return "ok"
                if r.status == 401:
                    self.bad_pins.append(time.time())
                    return "bad_pin"
                if r.status == 503:
                    return "not_configured"
                return "check_failed"
        except Exception:
            return "check_failed"

    async def trends(self, word, period=90):
        period = 7 if period == 7 else 90          # 30 días = recorte de los 90 (misma curva diaria)
        k = f"{word.lower()}|{period}"
        c = self.tcache.get(k)
        if c and time.time() - c[0] < TRENDS_TTL:
            return c[1], c[2]
        loop = asyncio.get_running_loop()
        data, errs = await asyncio.wait_for(loop.run_in_executor(None, trends_sync, word, period), 30)
        if any(t.get("points") for t in data.values()):
            self.tcache[k] = (time.time(), data, errs)
        return data, errs

    async def do_search(self, q, light=False):
        now = now_s()
        tag = to_tag(q)
        res = {"q": q, "tag": tag, "generated": now, "errors": [], "hashtag": None, "sample": None,
               "trends": None, "related": [], "box_version": VERSION}
        tterr = []
        t0 = time.time()
        await self.tt.ensure()

        async def tiktok_hashtag():
            if not tag:
                return []
            try:
                h = await self.tt.detail(tag)
            except Exception as e:
                tterr.append(f"detalle hashtag: {e}")
                return []
            res["hashtag"] = h
            if not h.get("found"):
                return []
            try:
                items = [video(x, "hashtag") for x in await self.tt.hashtag_items(h["id"], 1 if light else HASHTAG_PAGES)]
            except Exception as e:
                tterr.append(f"vídeos del hashtag: {e}")
                return []
            # hashtag pequeño: TikTok rellena la lista con vídeos que no tienen nada que ver
            if h.get("videos") is not None and len(items) > h["videos"] + 10:
                items = [v for v in items if relevant(v, q, tag)]
            return items

        async def tiktok_search():
            if light:
                return []
            try:
                return [video(x, "busqueda") for x in await self.tt.search_items(q, SEARCH_PAGES)]
            except Exception as e:
                tterr.append(f"búsqueda TikTok: {e}")
                return []

        async def g_trends():
            if light:
                return
            try:
                data, errs = await self.trends(q)
                res["trends"] = data
                res["errors"] += errs
            except Exception as e:
                res["errors"].append(f"google trends: {type(e).__name__}")

        async def related():
            if light:
                return
            try:
                res["related_tikwm"] = await tikwm_related(self.http, q)
            except Exception as e:
                res["errors"].append(f"tikwm: {e}")
                res["related_tikwm"] = []

        async def tiktok_all():
            hv, sv = await asyncio.gather(tiktok_hashtag(), tiktok_search())
            retried = False
            # TikTok falló y no hay vídeos del hashtag (200 vacíos, 403, plantilla caducada…): Chrome nuevo y un reintento
            if not hv and tterr and not light and time.time() - t0 < SEARCH_BUDGET - 22:
                log.warning("búsqueda '%s': TikTok sin datos (%s) → reinicio Chrome y reintento", q, " · ".join(tterr))
                retried = True
                try:
                    async with self.tt.lock:
                        await self.tt.restart()
                    first_h, first_sv = res["hashtag"], sv
                    tterr.clear()
                    res["hashtag"] = None
                    hv, sv = await asyncio.gather(tiktok_hashtag(), tiktok_search())
                    # lo que sí llegó en el primer intento no se pierde
                    if not sv and first_sv:
                        sv = first_sv
                        tterr[:] = [e for e in tterr if not e.startswith("búsqueda TikTok")]
                    if res["hashtag"] is None and first_h:
                        res["hashtag"] = first_h
                        tterr[:] = [e for e in tterr if not e.startswith("detalle hashtag")]
                except Exception as e:
                    tterr.append(f"reinicio de Chrome: {type(e).__name__}")
            return hv, sv, retried

        retried = timed_out = False
        try:
            (hv, sv, retried), _, _ = await asyncio.wait_for(asyncio.gather(tiktok_all(), g_trends(), related()), SEARCH_BUDGET)
        except asyncio.TimeoutError:
            res["errors"].append("tiempo agotado: resultados parciales")
            hv, sv, timed_out = [], [], True
        res["errors"] = tterr + res["errors"]
        # la búsqueda de TikTok es "difusa": solo cuentan los vídeos que mencionan de verdad la palabra
        sv_raw = len(sv)
        sv = [v for v in sv if relevant(v, q, tag)]
        allv = list({v["id"]: v for v in hv + sv if v["id"]}.values())
        res["sample"] = analyse(allv, now)
        res["tiktok_days"] = tiktok_days(allv, now)
        res["first_viral"] = first_viral(allv, now)
        res["rise"] = rise_start(allv, now)
        res["sample"]["from_hashtag"] = len(hv)
        res["sample"]["from_search"] = len(sv)
        res["sample"]["search_discarded"] = sv_raw - len(sv)
        # ok = hay vídeos · vacio = TikTok respondió pero no hay vídeos de esta palabra · fallo = TikTok no respondió bien
        res["tiktok"] = {"status": "ok" if allv else ("fallo" if tterr or timed_out else "vacio"),
                         "retried": retried, "search_raw": sv_raw, "errors": len(tterr)}
        # si TikTok no dio el detalle, respaldo con tikwm (mismo dato: vídeos y views del hashtag)
        tw = res.get("related_tikwm") or []
        if (not res["hashtag"] or res["hashtag"].get("found") is None) and tw:
            m = next((x for x in tw if x["tag"].lower() == tag), None)
            if m:
                res["hashtag"] = {"found": True, "tag": m["tag"], "id": m["id"], "videos": m["videos"], "views": m["views"],
                                  "url": "https://www.tiktok.com/tag/" + urllib.parse.quote(m["tag"]), "source": "tikwm"}
        # relacionados: hashtags que más se repiten en la muestra + los de tikwm
        cnt = {}
        for v in hv + sv:
            for t in set(v["tags"]):
                if t != tag and len(t) > 1 and t not in ("fyp", "foryou", "foryoupage", "viral", "fy", "parati", "fypシ", "xyzbca", "trending", "tiktok"):
                    cnt[t] = cnt.get(t, 0) + 1
        co = [{"tag": t, "count": c} for t, c in sorted(cnt.items(), key=lambda x: -x[1])[:15] if c >= 2]
        res["related"] = co
        flat = tag.replace("_", "")
        res["related_tikwm"] = [x for x in tw if x["tag"].lower() != tag and flat and flat in to_tag(x["tag"]).replace("_", "")][:10]
        g = self.store.growth(q)
        if g:
            res["growth"] = g
        res["verdict"] = verdict(res)
        if self.store.snap(q, res):
            res["growth"] = self.store.growth(q)
        return res

    async def search(self, q, force=False):
        k = q.lower()
        c = self.cache.get(k)
        if c and not force and time.time() - c[0] < CACHE_TTL:
            r = dict(c[1], cached=True, cache_age_s=int(time.time() - c[0]))
            g = self.store.growth(q)
            r["growth"] = g
            r["watching"] = bool(g)
            return 200, r
        if self.waiting >= MAX_QUEUE:
            return 429, {"error": "busy"}
        self.waiting += 1
        try:
            async with self.search_lock:
                gap = MIN_GAP - (time.time() - self.last_search)
                if gap > 0:
                    await asyncio.sleep(gap)
                try:
                    res = await self.do_search(q)
                finally:
                    self.last_search = time.time()
        finally:
            self.waiting -= 1
        # a la caché solo resultados sin fallos de TikTok (si falló, la siguiente búsqueda vuelve a intentarlo)
        tk = res.get("tiktok") or {}
        ok = tk.get("status") in ("ok", "vacio") and not tk.get("errors")
        if ok:
            self.cache[k] = (time.time(), res)
        if len(self.cache) > 300:
            for kk in sorted(self.cache, key=lambda x: self.cache[x][0])[:100]:
                self.cache.pop(kk, None)
        res = dict(res, cached=False, watching=bool(self.store.growth(q)))
        return 200, res

    def watchlist(self):
        out = []
        for k, w in self.store.d["watch"].items():
            s = w["snaps"][-1] if w["snaps"] else {}
            out.append({"q": w.get("q") or k, "added": w["added"], "snaps": len(w["snaps"]), "last": s, "growth": self.store.growth(k)})
        return sorted(out, key=lambda x: -x["added"])

    def set_watch(self, q, on):
        k = self.store.watch_key(q)
        if on:
            if k not in self.store.d["watch"]:
                if len(self.store.d["watch"]) >= MAX_WATCH:
                    return 400, {"error": "watch_full", "max": MAX_WATCH}
                self.store.d["watch"][k] = {"q": norm_q(q), "added": now_s(), "snaps": []}
                c = self.cache.get(k)
                self.store.save()
                if c:
                    self.store.snap(q, c[1])
        else:
            self.store.d["watch"].pop(k, None)
            self.store.save()
        return 200, {"ok": True, "watching": on, "watchlist": self.watchlist()}

    # ---- tareas de fondo (sin IA): fotos diarias de la watchlist y vigilancia del túnel
    async def snapshots_task(self):
        await asyncio.sleep(120)
        while True:
            try:
                for k, w in list(self.store.d["watch"].items()):
                    last = w["snaps"][-1]["t"] if w["snaps"] else 0
                    if now_s() - last < SNAPSHOT_EVERY or time.time() - self.last_user < 60:
                        continue
                    async with self.search_lock:
                        res = await self.do_search(w.get("q") or k, light=True)
                        self.last_search = time.time()
                    log.info("watchlist: foto de '%s' (%s vídeos)", k, (res.get("hashtag") or {}).get("videos"))
                    await asyncio.sleep(20)
            except Exception as e:
                log.warning("snapshots: %s", e)
            await asyncio.sleep(15 * 60)

    async def tunnel_task(self):
        """Watchdog del túnel cada ~30 s: arranca, rota antes de que caduque, vigila que la URL pública responda y
        publica box.json SOLO cuando la URL cambia (con espera creciente si GitHub falla)."""
        t = self.tunnel
        pub_fail = 0
        pub_next = 0.0
        while True:
            try:
                await t.reap()
                dead = t.proc is None or t.proc.returncode is not None or not t.url
                old_age = t.url and time.time() - getattr(t, "born", time.time()) > t.ROTATE_S
                if (dead or old_age) and time.time() >= t.next_try:
                    if dead:
                        log.warning("túnel caído o sin arrancar: lo levanto")
                    else:
                        log.info("túnel con %s min: lo rejuvenezco antes de que caduque", int((time.time() - t.born) / 60))
                    await t.start(self.http)
                elif t.url and not dead:
                    try:
                        async with self.http.get(t.url + "/health", timeout=ClientTimeout(total=12)) as r:
                            t.bad = 0 if r.status == 200 else t.bad + 1
                    except Exception:
                        t.bad += 1
                    if t.bad >= 2:
                        log.warning("la URL pública no responde (%s fallos seguidos): rehago el túnel", t.bad)
                        await t._kill(t.proc)
                        t.proc = None
                        t.next_try = 0.0
                        continue
                if t.url and t.published != t.url and time.time() >= pub_next:
                    if await t.publish(self.http):
                        pub_fail = 0
                    else:
                        pub_fail += 1
                        pub_next = time.time() + min(30 * 2 ** (pub_fail - 1), 600)
            except Exception as e:
                log.warning("túnel: %s", e)
            await asyncio.sleep(30)

    # ---- HTTP
    def json(self, code, obj):
        return web.json_response(obj, status=code, dumps=lambda o: json.dumps(o, ensure_ascii=False),
                                 headers={"Cache-Control": "no-store"})

    async def h_health(self, req):
        return self.json(200, {"ok": True, "service": "buscador", "version": VERSION, "browser": bool(self.tt.page),
                               "uptime_s": int(time.time() - self.started), "watch": len(self.store.d["watch"]),
                               "tiktok": {"last_ok_s_ago": int(time.time() - self.tt.last_ok) if self.tt.last_ok else None,
                                          "fails_in_row": self.tt.fails, "restarts": self.tt.restarts,
                                          "template": bool(self.tt.template)}})

    async def guarded(self, req):
        st = await self.pin_ok(req.headers.get("X-Radar-Pin", ""))
        if st == "ok":
            return None
        code = {"bad_pin": 401, "locked": 429}.get(st, 503)   # sin 502/504: Cloudflare los cambia por su página de error
        return self.json(code, {"error": st})

    async def h_search(self, req):
        if (g := await self.guarded(req)):
            return g
        q = norm_q(req.query.get("q"))
        if not q or not to_tag(q) and len(q) < 2:
            return self.json(400, {"error": "bad_query"})
        self.last_user = time.time()
        t = time.time()
        code, res = await self.search(q, force=req.query.get("fresh") == "1")
        if code == 200:
            res["took_s"] = round(time.time() - t, 1)
            log.info("búsqueda '%s' → %s (%.1fs, %s) · tiktok %s%s · n=%s · #%s · errores: %s", q,
                     (res.get("verdict") or {}).get("verdict"), time.time() - t, "caché" if res.get("cached") else "nueva",
                     (res.get("tiktok") or {}).get("status"), " (reintentado)" if (res.get("tiktok") or {}).get("retried") else "",
                     (res.get("sample") or {}).get("n"), (res.get("hashtag") or {}).get("videos"),
                     " · ".join(res.get("errors") or []) or "ninguno")
        return self.json(code, res)

    async def h_trends(self, req):
        if (g := await self.guarded(req)):
            return g
        q = norm_q(req.query.get("q"))
        try:
            period = int(req.query.get("period") or 90)
        except ValueError:
            period = 90
        if not q or period not in (7, 30, 90):
            return self.json(400, {"error": "bad_query"})
        self.last_user = time.time()
        try:
            data, errs = await self.trends(q, period)
        except Exception as e:
            return self.json(200, {"q": q, "period": period, "trends": None, "errors": [f"google trends: {type(e).__name__}"]})
        return self.json(200, {"q": q, "period": period, "trends": data, "errors": errs})

    async def h_watch(self, req):
        if (g := await self.guarded(req)):
            return g
        try:
            body = await req.json()
        except Exception:
            body = {}
        q = norm_q(body.get("q"))
        if not q:
            return self.json(400, {"error": "bad_query"})
        code, out = self.set_watch(q, bool(body.get("on")))
        return self.json(code, out)

    async def h_watchlist(self, req):
        if (g := await self.guarded(req)):
            return g
        return self.json(200, {"watchlist": self.watchlist()})

    async def on_start(self, app):
        self.http = ClientSession(timeout=ClientTimeout(total=20))
        try:
            await self.tt.start()
        except Exception as e:
            log.error("Chrome no arrancó: %s (se reintentará en la primera búsqueda)", e)
        loop = asyncio.get_running_loop()
        self.tasks = [loop.create_task(self.snapshots_task())]
        if TUNNEL:
            self.tasks.append(loop.create_task(self.tunnel_task()))

    async def on_stop(self, app):
        for t in getattr(self, "tasks", []):
            t.cancel()
        await self.tunnel.stop()
        try:
            if self.tt.ctx:
                await self.tt.ctx.close()
            if self.tt.pw:
                await self.tt.pw.stop()
        except Exception:
            pass
        await self.http.close()


def main():
    (ROOT / "logs").mkdir(exist_ok=True)
    pidf = os.environ.get("BUSCADOR_PIDFILE")
    if pidf:
        Path(pidf).write_text(str(os.getpid()))
    svc = Service()
    app = web.Application(client_max_size=64 * 1024)
    app.add_routes([web.get("/health", svc.h_health), web.get("/search", svc.h_search),
                    web.post("/watch", svc.h_watch), web.get("/watchlist", svc.h_watchlist),
                    web.get("/trends", svc.h_trends)])
    app.on_startup.append(svc.on_start)
    app.on_cleanup.append(svc.on_stop)
    log.info("Buscador en 127.0.0.1:%s (túnel %s, publicar %s)", PORT, "sí" if TUNNEL else "no", "sí" if PUBLISH else "no")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None, handle_signals=True, access_log=None)


if __name__ == "__main__":
    main()

"""Avisos de Telegram de TikTok Radar (sin IA, solo stdlib).

Se usa desde dos sitios (solo en el box, nunca en GitHub Actions):
  - al final de cada pasada del collector (radar.py, ~cada 8 min)
  - desde el vigilante rápido collector/fastwatch.py (cada ~45 s): avisa en cuanto detecta el evento
Qué hace:
  - capta el chat de Alex: si aún no hay chat guardado, el primer chat PRIVADO que escriba al bot (/start)
  - avisa "💰 DEX PAID" y "🎓 BONDING" una sola vez por coin, SOLO de coins con un link de TikTok asociado
    (vídeo/foto/enlace corto o perfil; no la cuenta oficial @tiktok ni búsquedas/tags). El nombre/ticker solo NO avisa.
    Solo eventos frescos (≤ alerts.fresh_minutes, 30): lo más antiguo se da por visto sin avisar.
    BONDING real: pump.fun complete=True, o un pool AMM creado DESPUÉS de que el radar viera la coin en bonding curve.
    El panel sigue mostrando más coins.
  - comandos /estado /pausa /reanudar (los responde el vigilante rápido en ≤ ~1 min)
Los dos procesos comparten el registro de avisos enviados con un candado de fichero (state/telegram.json.lock).
Token: variable de entorno TELEGRAM_BOT_TOKEN_TIKTOK_RADAR (nunca se imprime ni se guarda).
Estado: state/telegram.json (fuera del repo y NO se publica en la rama data).
"""
import fcntl, html, json, os, re, time, urllib.request, urllib.error
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TG_STATE = os.environ.get("RADAR_TG_STATE", os.path.join(ROOT, "state", "telegram.json"))
TOKEN_VAR = "TELEGRAM_BOT_TOKEN_TIKTOK_RADAR"
PANEL = "https://tiktok-radar-web.vercel.app"
CURVE_DEXES = {"pumpfun", "meteoradbc", "raydium-launchlab", "launchlab", "moonshot", "boop", "believe", "letsbonk"}
TG_LOCK = TG_STATE + ".lock"
FRESH_MS = 30 * 60_000  # por defecto; config alerts.fresh_minutes
# link de TikTok asociado (lo único que hace avisar): vídeo/foto/enlace corto o perfil (tiktok.com/@cuenta)
POST_LINK = re.compile(r"tiktok\.com/@([^/?#\s]+)/(video|photo)/\d+", re.I)
SHORT_LINK = re.compile(r"(?:vm|vt)\.tiktok\.com/\w+|tiktok\.com/t/\w+|m\.tiktok\.com/v/\d+", re.I)
PROFILE_LINK = re.compile(r"tiktok\.com/@([^/?#\s]+)/?(?:[?#]|$)", re.I)
# cuentas oficiales de TikTok: no cuentan como "link asociado" (config alerts.excluded_accounts las amplía)
EXCLUDED_ACCOUNTS = {"tiktok", "tiktok_us", "tiktok_uk", "tiktokshop", "tiktokcreators", "tiktoknewsroom",
                     "tiktokforbusiness", "tiktokforgood"}
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Madrid")
except Exception:  # pragma: no cover
    TZ = timezone(timedelta(hours=2))

def log(*a):
    print(time.strftime("%H:%M:%S"), "[telegram]", *a, flush=True)

def tiktok_link(c):
    """Link de TikTok asociado a la coin (lo que hace avisar). Devuelve (tipo, url, cuenta) con tipo
    video/photo/short/profile (prefiere vídeo/foto a perfil) o None. No cuentan: la cuenta oficial @tiktok (y otras
    oficiales), búsquedas, tags, música, efectos… ni el nombre/ticker/descripción/categoría."""
    profile = None
    for u in (c.get("links") or {}).get("tiktok") or []:
        u = u or ""
        m = POST_LINK.search(u)
        if m:
            if m.group(1).lower() not in EXCLUDED_ACCOUNTS:
                return (m.group(2).lower(), u, m.group(1))
            continue
        if SHORT_LINK.search(u):
            return ("short", u, None)
        m = PROFILE_LINK.search(u)
        if m and not profile and m.group(1).lower() not in EXCLUDED_ACCOUNTS:
            profile = ("profile", u, m.group(1))
    return profile

strong_signal = tiktok_link  # nombre antiguo (lo usa fastwatch.py)

def bonding_event(c):
    """BONDING real -> hora de creación del pool AMM (pair_ts), o None.
    Vale si pump.fun dice complete=True, o si el pool se creó DESPUÉS de que el radar viera la coin en bonding curve
    (curve_seen). Así no cuenta un pool que ya existía desde el lanzamiento (falsos 'bonding' de meteoradbc)."""
    dex, pt = c.get("dex"), c.get("pair_ts")
    if not dex or dex in CURVE_DEXES or not pt:
        return None
    if (c.get("pump") or {}).get("complete") is True:
        return pt
    cs = c.get("curve_seen")
    return pt if cs and pt > cs else None

@contextmanager
def locked():
    """Candado compartido por la pasada lenta y el vigilante rápido (evita avisos duplicados)."""
    os.makedirs(os.path.dirname(TG_STATE), exist_ok=True)
    with open(TG_LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def load_state():
    try:
        with open(TG_STATE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_state(s):
    os.makedirs(os.path.dirname(TG_STATE), exist_ok=True)
    tmp = TG_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TG_STATE)

class TG:
    def __init__(self, token):
        self._t = token
        self.last_send = 0.0
        self.sent = 0
    def call(self, method, _http_timeout=20, **params):
        req = urllib.request.Request(f"https://api.telegram.org/bot{self._t}/{method}",
                                     data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=_http_timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                try:
                    body = json.loads(e.read())
                except Exception:
                    body = {}
                ra = (body.get("parameters") or {}).get("retry_after")
                if e.code == 429 and ra and attempt == 0:
                    time.sleep(min(int(ra), 30) + 1); continue
                return {"ok": False, "error_code": e.code, "description": body.get("description")}
            except Exception as e:  # red, timeout...: no rompe la pasada (el error NO incluye la URL con el token)
                return {"ok": False, "description": type(e).__name__}
        return {"ok": False}
    def send(self, chat_id, text):
        wait = 1.1 - (time.time() - self.last_send)  # Telegram: ~1 mensaje/s por chat
        if wait > 0:
            time.sleep(wait)
        r = self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                      link_preview_options={"is_disabled": True})
        self.last_send = time.time()
        if r.get("ok"):
            self.sent += 1
        else:
            log("envío fallido:", r.get("error_code"), r.get("description"))
        return bool(r.get("ok"))

# ---------------------------------------------------------------- formato
def money(v):
    if not v:
        return "–"
    return f"${v/1e9:.2f}B" if v >= 1e9 else f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.1f}K" if v >= 1e3 else f"${v:.0f}"

def age(ms, now):
    if not ms:
        return "–"
    m = max(0, (now - ms) / 60000)
    return f"{m:.0f} min" if m < 60 else f"{m/60:.1f} h"

def e(s):
    return html.escape(str(s or ""), quote=True)

REASON = {"kw_name": "TikTok en el nombre", "kw_desc": "TikTok en la descripción", "link_tiktok": "Link de TikTok",
          "meta_tiktok": "Categoría TikTok de DexScreener"}

def hhmm(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%H:%M")

def coin_text(c, kind, now, test=False, event_ts=None):
    m = c.get("metrics") or {}
    L = c.get("links") or {}
    head = {"dex_paid": "💰 <b>DEX PAID</b>", "bonding": "🎓 <b>BONDING completada</b>"}[kind]
    lines = [head + (" <i>(prueba)</i>" if test else "") + " · TikTok Radar"]
    title = f"<b>{e(c.get('name') or '?')}</b> ${e(c.get('symbol') or '?')}"
    if c.get("dev_hot"):
        title += f"  🔥 TikTok dev ({e(c.get('dev_count'))})"
    lines.append(title)
    lines.append(f"<code>{e(c['ca'])}</code>")
    lines.append(f"MC <b>{money(m.get('mc'))}</b> · ATH {money(c.get('ath'))} · Liq {money(m.get('liq'))} · "
                 f"edad {age(c.get('created') or c.get('first_seen'), now)}")
    ev = event_ts or (c.get("dex_paid_at") if kind == "dex_paid" else None)
    if ev and kind == "dex_paid" and now - ev > FRESH_MS:
        lines.append(f"⏱️ DEX aprobado hace poco (el pago es de las {hhmm(ev)})")  # la revisión tardó
    elif ev:
        lines.append(f"⏱️ {'Pagó DEX' if kind == 'dex_paid' else 'Migró'} a las {hhmm(ev)} (hace {age(ev, now)})")
    sig = tiktok_link(c)
    if sig:
        label = {"video": "vídeo de TikTok", "photo": "foto de TikTok", "short": "vídeo de TikTok"}.get(sig[0]) \
            or f"perfil de TikTok @{sig[2]}"
        url = sig[1] if sig[1].startswith("http") else "https://" + sig[1]
        lines.append(f'TikTok: <a href="{e(url)}">{e(label)}</a>')
    links = []
    for key, name in (("dexscreener", "DexScreener"), ("pumpfun", "pump.fun"), ("gmgn", "GMGN")):
        if L.get(key):
            links.append(f'<a href="{e(L[key])}">{name}</a>')
    links.append(f'<a href="{PANEL}">Panel</a>')
    lines.append(" · ".join(links))
    return "\n".join(lines)

# ---------------------------------------------------------------- eventos
def event_ts(c, kind):
    return c.get("dex_paid_at") if kind == "dex_paid" else bonding_event(c)

def fresh_ts(c, kind):
    """Hora con la que se mide la frescura. DEX PAID: la aprobación puede llegar después del pago, así que se usa
    max(pago, última comprobación en la que aún NO estaba pagada) (dex_fresh_at, lo calcula la pasada)."""
    if kind == "dex_paid":
        return c.get("dex_fresh_at") or c.get("dex_paid_at")
    return bonding_event(c)

def handle_updates(tg, st, data, cfg):
    r = tg.call("getUpdates", offset=st.get("offset", 0), timeout=0, allowed_updates=["message"])
    if not r.get("ok"):
        log("getUpdates falló:", r.get("error_code"), r.get("description")); return
    for u in r.get("result") or []:
        st["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        text = (msg.get("text") or "").strip().split("@")[0].lower()
        if chat.get("type") != "private":
            continue
        if not st.get("chat_id"):
            st["chat_id"] = chat["id"]
            st["chat_user"] = (msg.get("from") or {}).get("username")
            st["connected_at"] = int(time.time() * 1000)
            st["welcome_pending"] = True
            log("chat captado")
            continue
        if chat["id"] != st["chat_id"]:
            continue  # solo se habla con el chat de Alex
        if text in ("/start",):
            st["welcome_pending"] = True
        elif text in ("/pausa", "/pause"):
            st["paused"] = True
            tg.send(st["chat_id"], "⏸️ Avisos en pausa. /reanudar para volver a activarlos.")
        elif text in ("/reanudar", "/resume"):
            st["paused"] = False
            tg.send(st["chat_id"], "▶️ Avisos activados de nuevo.")
        elif text in ("/estado", "/status"):
            g = data.get("generated_ms") or 0
            when = datetime.fromtimestamp(g / 1000, TZ).strftime("%d/%m %H:%M") if g else "–"
            tg.send(st["chat_id"],
                    f"📡 <b>TikTok Radar</b>\nÚltima pasada: <b>{when}</b> (hora de Madrid)\n"
                    f"Coins TikTok &lt; {data.get('max_age_hours', 24)} h: <b>{data.get('total', 0)}</b> · "
                    f"DEX PAID: {data.get('dex_paid', 0)}\n"
                    f"Avisos: {'⏸️ en pausa' if st.get('paused') else '✅ activos'} · enviados en total: "
                    f"{st.get('sent_total', 0)}\n<a href=\"{PANEL}\">Abrir panel</a>")

def deliver(tg, st, kind, c, now, A, event_ts=None, fresh_at=None):
    """Envía UN aviso si toca (llamar con el candado cogido). Devuelve sent/dup/paused/low/fail."""
    ca = c["ca"]
    if ca in st["sent"][kind]:
        return "dup"
    fresh = (A.get("fresh_minutes", 30) or 30) * 60_000
    ref = fresh_at or event_ts
    if not ref or now - ref > fresh:
        st["sent"][kind][ca] = now  # evento antiguo (o sin hora): se da por visto, sin avisar
        st.setdefault("stale", {})[ca + ":" + kind] = ref
        return "stale"
    if st.get("paused"):
        st["sent"][kind][ca] = now; return "paused"  # en pausa: se dan por vistos (sin avalancha al reanudar)
    if ((c.get("metrics") or {}).get("mc") or 0) < (A.get("min_mc", 0) or 0):
        return "low"  # se avisará si más adelante supera el mínimo
    # anti-rug en el momento del aviso (Jupiter fresco): 🧹 chart falso / 🤖 volumen bot → no se avisa.
    # Si Jupiter falla, se avisa igual. No se marca como enviado: si deja de cumplir la regla (y sigue fresco), se avisa.
    try:
        import antirug
        why = antirug.alert_check(c)
    except Exception:
        why = []
    if why:
        k = ca + ":" + kind
        fl = st.setdefault("filtered", {})
        if k not in fl:
            log(f"aviso {kind} omitido por anti-rug ({', '.join(why)}): {c.get('name')} ${c.get('symbol')} {ca}")
        fl[k] = {"at": now, "why": why, "name": c.get("name")}
        st["filtered"] = dict(list(fl.items())[-300:])
        return "filtered"
    if tg.send(st["chat_id"], coin_text(c, kind, now, event_ts=event_ts)):
        st["sent"][kind][ca] = now
        st["sent_total"] = st.get("sent_total", 0) + 1
        st.setdefault("log", []).append({"ca": ca, "kind": kind, "at": now, "event": event_ts or
                                         (c.get("dex_paid_at") if kind == "dex_paid" else None)})
        st["log"] = st["log"][-50:]
        return "sent"
    return "fail"

def _defaults(st):
    st.setdefault("sent", {"dex_paid": {}, "bonding": {}})
    st.pop("last_dex", None)  # ya no se usa (BONDING real = bonding_event)
    st["stale"] = dict(list((st.get("stale") or {}).items())[-300:])
    return st

def enabled_kinds(cfg):
    A = cfg.get("alerts") or {}
    EXCLUDED_ACCOUNTS.update(a.lower().lstrip("@") for a in A.get("excluded_accounts") or [])
    if not A.get("enabled", False) or os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("RADAR_ALERTS") == "0":
        return A, []
    return A, [k for k, on in (("dex_paid", A.get("dex_paid", True)), ("bonding", A.get("bonding", True))) if on]

def alert_now(kind, c, cfg, event_ts=None, tg=None, fresh_at=None):
    """Aviso inmediato desde el vigilante rápido. Respeta link de TikTok, frescura, pausa, mínimo de MC y el registro compartido."""
    A, kinds = enabled_kinds(cfg)
    if kind not in kinds:
        return "off"
    if not tiktok_link(c):
        return "weak"
    token = os.environ.get(TOKEN_VAR)
    if not token:
        return "notoken"
    tg = tg or TG(token)
    now = int(time.time() * 1000)
    with locked():
        st = _defaults(load_state())
        if not st.get("chat_id") or not st.get("seeded"):
            return "nochat"
        r = deliver(tg, st, kind, c, now, A, event_ts, fresh_at)
        if r in ("sent", "paused", "stale", "filtered"):
            save_state(st)
    return r

def poll_commands(data, cfg, tg=None):
    """Responde /start /estado /pausa /reanudar y capta el chat (desde el vigilante rápido)."""
    token = os.environ.get(TOKEN_VAR)
    A, kinds = enabled_kinds(cfg)
    if not token or not kinds:
        return
    tg = tg or TG(token)
    with locked():
        st = _defaults(load_state())
        before = json.dumps(st, sort_keys=True)
        handle_updates(tg, st, data, cfg)
        welcome(tg, st)
        if json.dumps(st, sort_keys=True) != before:
            save_state(st)

def welcome(tg, st):
    chat = st.get("chat_id")
    if chat and st.pop("welcome_pending", False):
        tg.send(chat, "✅ <b>TikTok Radar conectado</b>\nTe avisaré aquí cuando una coin claramente TikTok de Solana:\n"
                      "💰 pague DEX (DEX PAID)\n🎓 complete la bonding curve de pump.fun\n\n"
                      "Comandos: /estado · /pausa · /reanudar\n"
                      f"<a href=\"{PANEL}\">Abrir panel</a>")

def process(coins, data, cfg, now=None):
    """Una vez por pasada. Nunca lanza excepción hacia el collector."""
    A, kinds = enabled_kinds(cfg)  # avisos solo desde el box (evita duplicados con la Action de respaldo)
    if not kinds:
        return
    token = os.environ.get(TOKEN_VAR)
    if not token:
        log("sin token en el entorno: avisos desactivados en esta pasada"); return
    now = now or int(time.time() * 1000)
    tg = TG(token)
    with locked():
        _process_locked(tg, coins, data, cfg, A, kinds, now)

def _process_locked(tg, coins, data, cfg, A, kinds, now):
    st = _defaults(load_state())
    try:
        handle_updates(tg, st, data, cfg)
        welcome(tg, st)
        chat = st.get("chat_id")
        events = []
        for ca, c in coins.items():
            if c.get("dead") or not tiktok_link(c):
                continue  # avisos solo de coins con link de TikTok asociado
            if "dex_paid" in kinds and c.get("dex_paid") and ca not in st["sent"]["dex_paid"]:
                events.append(("dex_paid", c))
            if "bonding" in kinds and bonding_event(c) and ca not in st["sent"]["bonding"]:
                events.append(("bonding", c))
        if chat and not st.get("seeded"):
            st["seeded"] = True  # lo antiguo lo descarta el límite de frescura (deliver)
        if chat:
            max_per = A.get("max_per_run", 15)
            events.sort(key=lambda kc: -((kc[1].get("metrics") or {}).get("mc") or 0))
            for kind, c in events:
                if tg.sent >= max_per:
                    break  # el resto en la próxima pasada
                deliver(tg, st, kind, c, now, A, event_ts(c, kind), fresh_ts(c, kind))
        # limpieza (coins que ya no seguimos)
        for kind in st["sent"]:
            for ca in [ca for ca, t in st["sent"][kind].items() if ca not in coins and now - t > 3 * 86_400_000]:
                st["sent"][kind].pop(ca, None)
        if tg.sent:
            log(f"{tg.sent} mensajes enviados")
    except Exception as ex:  # nunca rompe la pasada
        log("error:", type(ex).__name__, str(ex)[:200])
    finally:
        save_state(st)

def send_test(coins, cfg, kind="dex_paid"):
    """Envía un aviso de prueba con una coin real (marcado '(prueba)'). Uso: python3 collector/alerts.py --test"""
    token = os.environ.get(TOKEN_VAR)
    st = load_state()
    if not token or not st.get("chat_id"):
        print("falta token o chat_id"); return False
    pool = [c for c in coins.values() if c.get("dex_paid")] or list(coins.values())
    c = max(pool, key=lambda c: (c.get("metrics") or {}).get("mc") or 0)
    return TG(token).send(st["chat_id"], coin_text(c, kind, int(time.time() * 1000), test=True))

if __name__ == "__main__":
    import sys
    cfgp = os.path.join(ROOT, "collector", "config.json")
    statep = os.environ.get("RADAR_STATE", os.path.join(ROOT, "state", "state.json"))
    if "--test" in sys.argv:
        cfg = json.load(open(cfgp)); coins = json.load(open(statep)).get("coins", {})
        print("enviado" if send_test(coins, cfg, "bonding" if "--bonding" in sys.argv else "dex_paid") else "no enviado")

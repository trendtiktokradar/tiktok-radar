"""Avisos de Telegram de TikTok Radar (sin IA, solo stdlib).

Se llama al final de cada pasada del collector (solo en el box, nunca en GitHub Actions):
  - capta el chat de Alex: si aún no hay chat guardado, el primer chat PRIVADO que escriba al bot (/start)
  - avisa "💰 DEX PAID" y "🎓 BONDING" una sola vez por coin (solo coins que pasan el filtro TikTok del panel)
  - comandos (se responden en la siguiente pasada, ≤ ~8 min): /estado /pausa /reanudar
Token: variable de entorno TELEGRAM_BOT_TOKEN_TIKTOK_RADAR (nunca se imprime ni se guarda).
Estado: state/telegram.json (fuera del repo y NO se publica en la rama data).
"""
import html, json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TG_STATE = os.environ.get("RADAR_TG_STATE", os.path.join(ROOT, "state", "telegram.json"))
TOKEN_VAR = "TELEGRAM_BOT_TOKEN_TIKTOK_RADAR"
PANEL = "https://tiktok-radar-web.vercel.app"
CURVE_DEXES = {"pumpfun", "meteoradbc", "raydium-launchlab", "launchlab", "moonshot", "boop", "believe", "letsbonk"}
RECENT_MS = 15 * 60_000
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Madrid")
except Exception:  # pragma: no cover
    TZ = timezone(timedelta(hours=2))

def log(*a):
    print(time.strftime("%H:%M:%S"), "[telegram]", *a, flush=True)

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

def coin_text(c, kind, now, test=False):
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
    why = []
    for r in c.get("reasons") or []:
        if not r.get("q"):
            continue
        label = REASON.get(r["t"], r.get("d") or r["t"])
        if r["t"] == "kw_name" and r.get("k"):
            label = f"'{r['k'].split(':', 1)[1]}' en el nombre"
        if r["t"] == "link_tiktok" and (L.get("tiktok") or []):
            u = L["tiktok"][0]
            if u.startswith("http"):
                label = f'<a href="{e(u)}">Link de TikTok</a>'
                why.append(label); continue
        why.append(e(label))
    if why:
        lines.append("TikTok: " + " · ".join(dict.fromkeys(why)))
    links = []
    for key, name in (("dexscreener", "DexScreener"), ("pumpfun", "pump.fun"), ("gmgn", "GMGN")):
        if L.get(key):
            links.append(f'<a href="{e(L[key])}">{name}</a>')
    links.append(f'<a href="{PANEL}">Panel</a>')
    lines.append(" · ".join(links))
    return "\n".join(lines)

# ---------------------------------------------------------------- eventos
def is_bonded(c, last_dex):
    """Bonding curve completada: pump.fun dice complete=True, o la coin estaba en un DEX de bonding curve
    (pumpfun, meteoradbc, launchlab…) y su par principal en DexScreener pasa a un AMM (pumpswap, raydium, meteora…)."""
    if (c.get("pump") or {}).get("complete") is True:
        return True
    dex = c.get("dex")
    return bool(last_dex in CURVE_DEXES and dex and dex not in CURVE_DEXES)

def event_recent(c, kind, now):
    if kind == "dex_paid":
        t = c.get("dex_paid_at") or 0
    else:
        t = c.get("pair_ts") if c.get("dex") not in CURVE_DEXES else 0
    return bool(t and now - t <= RECENT_MS)

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

def process(coins, data, cfg, now=None):
    """Una vez por pasada. Nunca lanza excepción hacia el collector."""
    A = cfg.get("alerts") or {}
    if not A.get("enabled", False):
        return
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("RADAR_ALERTS") == "0":
        return  # avisos solo desde el box (evita duplicados con la Action de respaldo)
    token = os.environ.get(TOKEN_VAR)
    if not token:
        log("sin token en el entorno: avisos desactivados en esta pasada"); return
    now = now or int(time.time() * 1000)
    tg = TG(token)
    st = load_state()
    st.setdefault("sent", {"dex_paid": {}, "bonding": {}})
    st.setdefault("last_dex", {})
    try:
        handle_updates(tg, st, data, cfg)
        chat = st.get("chat_id")
        if chat and st.pop("welcome_pending", False):
            tg.send(chat, "✅ <b>TikTok Radar conectado</b>\nTe avisaré aquí cuando una coin TikTok de Solana:\n"
                          "💰 pague DEX (DEX PAID)\n🎓 complete la bonding curve de pump.fun\n\n"
                          "Comandos: /estado · /pausa · /reanudar\n"
                          f"<a href=\"{PANEL}\">Abrir panel</a>")
        kinds = [k for k, on in (("dex_paid", A.get("dex_paid", True)), ("bonding", A.get("bonding", True))) if on]
        events = []
        for ca, c in coins.items():
            if c.get("dead"):
                continue
            if "dex_paid" in kinds and c.get("dex_paid") and ca not in st["sent"]["dex_paid"]:
                events.append(("dex_paid", c))
            if "bonding" in kinds and is_bonded(c, st["last_dex"].get(ca)) and ca not in st["sent"]["bonding"]:
                events.append(("bonding", c))
        if chat and not st.get("seeded"):
            # primera vez: no avisar de lo que ya estaba pagado/graduado, salvo si pasó en los últimos 15 min
            n = 0
            for kind, c in events:
                if not event_recent(c, kind, now):
                    st["sent"][kind][c["ca"]] = now; n += 1
            st["seeded"] = True
            log(f"primera conexión: {n} eventos antiguos marcados como ya vistos")
            events = [(k, c) for k, c in events if c["ca"] not in st["sent"][k]]
        if chat:
            min_mc = A.get("min_mc", 0) or 0
            max_per = A.get("max_per_run", 15)
            events.sort(key=lambda kc: -((kc[1].get("metrics") or {}).get("mc") or 0))
            for kind, c in events:
                if st.get("paused"):
                    st["sent"][kind][c["ca"]] = now; continue  # en pausa: se dan por vistos (sin avalancha al reanudar)
                if ((c.get("metrics") or {}).get("mc") or 0) < min_mc:
                    continue  # se avisará si más adelante supera el mínimo
                if tg.sent >= max_per:
                    break  # el resto en la próxima pasada
                if tg.send(chat, coin_text(c, kind, now)):
                    st["sent"][kind][c["ca"]] = now
                    st["sent_total"] = st.get("sent_total", 0) + 1
        for ca, c in coins.items():
            if c.get("dex"):
                st["last_dex"][ca] = c["dex"]
        # limpieza (coins que ya no seguimos)
        for ca in [ca for ca in st["last_dex"] if ca not in coins]:
            st["last_dex"].pop(ca, None)
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

"""Filtro anti-rug (reglas fijas, sin IA). Lo usan la pasada (collector/radar.py) y los avisos (collector/alerts.py).

Datos: API web pública de Jupiter (datapi.jup.ag, sin login): hasta 100 CAs por llamada, ~0,25 s. De ahí sale
"fees" = SOL totales pagados en comisiones por los traders de la coin (prioridad + propinas + bots), como el
"Fees" de Axiom/GMGN, además de holders, traders, volumen, etc.

Reglas (una coin que cumpla alguna NO sale en la web; en los avisos solo cuentan 🧹 y 🤖):
  🧹 fake_chart  MC ≥ $10K y fees < 1 SOL por cada $30K de MC          (chart/MC falso, nadie opera de verdad)
  🤖 wash        volumen de toda su vida ≥ $5K y fees en USD < 0,3 % del volumen   (volumen de bots)
  💀 rug         ATH ≥ $30K y MC < 10 % del ATH
Si Jupiter no responde (o no trae la coin): no se filtra, salvo el respaldo de 🤖 con DexScreener
(tamaño medio de operación < $15 con ≥ 1000 transacciones en 24 h).
"""
import json
import urllib.parse
import urllib.request

JUP_URL = "https://datapi.jup.ag/v1/assets/search?query="
SOL_MINT = "So11111111111111111111111111111111111111112"
UA = "Mozilla/5.0 (X11; Linux x86_64) tiktok-radar"
BATCH = 100
LABELS = {"fake_chart": "🧹 chart falso", "wash": "🤖 volumen bot", "rug": "💀 rug"}

FAKE_MIN_MC = 10_000
FAKE_SOL_PER_MC = 1 / 30_000      # 1 SOL por cada $30K de MC
WASH_MIN_VOL = 5_000
WASH_MAX_FEE_RATIO = 0.003        # 0,3 %
RUG_MIN_ATH = 30_000
RUG_MAX_FRACTION = 0.10
FB_MAX_AVG_TRADE = 15
FB_MIN_TXNS = 1000


def _get(cas, timeout):
    q = ",".join(cas)
    req = urllib.request.Request(JUP_URL + urllib.parse.quote(q, safe=","), headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return d if isinstance(d, list) else []


def fetch(cas, timeout=15):
    """→ (tokens {ca: datos}, precio SOL en USD o None, nº de llamadas fallidas). Nunca lanza excepción."""
    cas = [c for c in dict.fromkeys(cas) if c]
    out, sol, bad = {}, None, 0
    chunks = [cas[i:i + BATCH - 1] for i in range(0, len(cas), BATCH - 1)] or [[]]
    for i, ch in enumerate(chunks):
        q = ch + ([SOL_MINT] if i == 0 else [])
        try:
            for x in _get(q, timeout):
                if x.get("id") == SOL_MINT:
                    sol = x.get("usdPrice") or sol
                elif x.get("id"):
                    out[x["id"]] = x
        except Exception:
            bad += 1
    return out, sol, bad


def compact(x, ts):
    """Lo que guardamos por coin (state.json) para auditar falsos positivos."""
    s = x.get("stats24h") or {}
    a = x.get("audit") or {}
    r = lambda v, n=2: round(v, n) if isinstance(v, (int, float)) else None
    return {"fees": r(x.get("fees"), 4), "vol": r((s.get("buyVolume") or 0) + (s.get("sellVolume") or 0), 0),
            "mc": r(x.get("mcap"), 0), "liq": r(x.get("liquidity"), 0), "holders": x.get("holderCount"),
            "traders": s.get("numTraders"), "buys": s.get("numBuys"), "sells": s.get("numSells"),
            "top10": r(a.get("topHoldersPercentage"), 1), "bot": r(a.get("botHoldersPercentage"), 1),
            "org": r(x.get("organicScore"), 1), "dev_mints": a.get("devMints"),
            "bundle": r((a.get("bundlerStats") or {}).get("holdingPctATH"), 3), "at": ts}


def reasons(c, j, sol, alert=False):
    """Motivos de filtro de una coin. j = datos de Jupiter (dict de compact() o crudo) o None si Jupiter falló.
    alert=True: solo 🧹/🤖 y sin respaldo (si Jupiter falla, el aviso se envía)."""
    m = c.get("metrics") or {}
    out = []
    if j and sol:
        if "stats24h" in j:            # crudo de la API
            j = compact(j, 0)
        mc = j.get("mc") or m.get("mc") or 0
        fees = j.get("fees") or 0      # sin campo fees = nunca ha operado nadie
        vol = j.get("vol") or 0
        if mc >= FAKE_MIN_MC and fees < mc * FAKE_SOL_PER_MC:
            out.append("fake_chart")
        if vol >= WASH_MIN_VOL and fees * sol < WASH_MAX_FEE_RATIO * vol:
            out.append("wash")
        ath = max(c.get("ath") or 0, mc)
        if not alert and ath >= RUG_MIN_ATH and mc < RUG_MAX_FRACTION * ath:
            out.append("rug")
    elif not alert:
        v24 = (m.get("vol") or {}).get("h24") or 0
        tx = (m.get("buys_h24") or 0) + (m.get("sells_h24") or 0)
        if tx >= FB_MIN_TXNS and v24 / tx < FB_MAX_AVG_TRADE:
            out.append("wash")
    return out


def alert_check(c, timeout=6):
    """En el momento del aviso: Jupiter fresco para esta coin. [] si está bien o si Jupiter falla (se avisa igual)."""
    try:
        d = _get([c["ca"], SOL_MINT], timeout)
    except Exception:
        return []
    j = next((x for x in d if x.get("id") == c["ca"]), None)
    sol = next((x.get("usdPrice") for x in d if x.get("id") == SOL_MINT), None)
    if not j or not sol:
        return []
    return reasons(c, j, sol, alert=True)

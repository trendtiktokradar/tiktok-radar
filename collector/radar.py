#!/usr/bin/env python3
"""TikTok Radar - collector (sin IA, solo Python stdlib).

Busca memecoins de Solana relacionadas con TikTok en fuentes públicas,
las fusiona con el histórico y genera web/data.json para el panel.

Uso:  python3 collector/radar.py            (una pasada)
      python3 collector/radar.py --no-trends (sin Creative Center)
"""
import json, math, os, re, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "collector", "config.json")
STATE_PATH = os.environ.get("RADAR_STATE", os.path.join(ROOT, "state", "state.json"))
DATA_PATH = os.environ.get("RADAR_DATA", os.path.join(ROOT, "web", "data.json"))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
DS = "https://api.dexscreener.com"
PUMP = "https://frontend-api-v3.pump.fun"
SOL_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TIKTOK_URL = re.compile(r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/[^\s\"'<>)]*", re.I)
QUOTE_MINTS = {"So11111111111111111111111111111111111111112",
               "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
               "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}

def now_ms():
    return int(time.time() * 1000)

def iso(ms=None):
    ms = now_ms() if ms is None else ms
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

# ---------------------------------------------------------------- HTTP
class Source:
    """Lleva la cuenta de qué fuentes funcionan en cada pasada."""
    def __init__(self):
        self.status = {}
    def mark(self, name, ok, n=0, err=None):
        s = self.status.setdefault(name, {"ok": 0, "fail": 0, "items": 0, "last_error": None})
        if ok:
            s["ok"] += 1; s["items"] += n
        else:
            s["fail"] += 1; s["last_error"] = str(err)[:200]

SRC = Source()

def http(url, source, data=None, headers=None, retries=3, timeout=25, soft404=False):
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode("utf-8", "replace"))
            return out
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 429:
                time.sleep(2 + attempt * 3); continue
            if e.code in (403, 404, 401):
                break  # bloqueado / no existe: no insistir
            time.sleep(1 + attempt)
        except Exception as e:  # timeout, DNS, JSON...
            last = repr(e)
            time.sleep(1 + attempt)
    if soft404 and last == "HTTP 404":
        return None  # "no existe" es una respuesta normal para esta fuente: no cuenta como fallo
    SRC.mark(source, False, err=f"{last} {url[:120]}")
    return None

# ---------------------------------------------------------------- utils
def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None

def norm(s):
    return re.sub(r"[^0-9a-z\u00c0-\uffff]", "", (s or "").lower())

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

# ---------------------------------------------------------------- matching (reglas, sin IA)
class Matcher:
    def __init__(self, cfg, trends):
        self.cfg = cfg
        self.strong = [(re.compile(p, re.I), label) for p, label in cfg["strong_patterns"]]
        self.phrases = [p.lower() for p in cfg["desc_phrases"]]
        stop = set(cfg.get("trend_stopwords", []))
        self.trend_tags = {}  # tag normalizado -> etiqueta legible
        for t in trends:
            tag = norm(t["name"])
            if len(tag) >= 5 and tag.isascii() and tag not in stop and not tag.isdigit():
                self.trend_tags.setdefault(tag, f"#{t['name']} ({t['country']})")
        self.manual = [(norm(m), m) for m in cfg.get("manual_trends", []) if len(norm(m)) >= 2]

    def match(self, name, symbol, desc, links):
        reasons = []
        text_ns = f"{name or ''} {symbol or ''}"
        desc = desc or ""
        for rx, label in self.strong:
            if rx.search(text_ns):
                reasons.append({"t": "kw_name", "k": f"kw_name:{label}", "d": f"'{label}' en nombre/ticker", "w": 3, "q": True}); break
        else:
            for rx, label in self.strong:
                if rx.search(desc):
                    reasons.append({"t": "kw_desc", "k": f"kw_desc:{label}", "d": f"Menciona '{label}' en la descripción", "w": 2, "q": True}); break
        tiktok_links = sorted({u.rstrip(".,") for u in TIKTOK_URL.findall(" ".join(links) + " " + desc)})
        if tiktok_links:
            reasons.append({"t": "link_tiktok", "d": "Link de TikTok en sus redes/descripción", "w": 3, "q": True})
        dl = desc.lower()
        ph = [p for p in self.phrases if p in dl]
        if ph and not any(r["t"] in ("kw_desc",) for r in reasons):
            reasons.append({"t": "phrase", "d": f"Frase viral en descripción: '{ph[0]}'", "w": 1 if len(ph) == 1 else 2})
        nn, ns = norm(name), norm(symbol)
        for tag, label in self.trend_tags.items():
            if nn == tag or ns == tag or (len(tag) >= 7 and tag in nn):
                reasons.append({"t": "trend", "d": f"Coincide con trend TikTok {label}", "w": 2}); break
        for m, label in self.manual:
            if m and (m == nn or m == ns or (len(m) >= 4 and m in nn)):
                reasons.append({"t": "manual", "d": f"Coincide con trend manual '{label}'", "w": 2}); break
        return reasons, tiktok_links

# ---------------------------------------------------------------- fuentes
def ds_pair_to_info(p):
    """Datos útiles de un par de DexScreener."""
    info = p.get("info") or {}
    links = [w.get("url", "") for w in info.get("websites") or []]
    socials = {}
    for s in info.get("socials") or []:
        socials.setdefault(s.get("type"), s.get("url"))
        links.append(s.get("url", ""))
    bt = p.get("baseToken") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    tx = p.get("txns") or {}
    return {
        "ca": bt.get("address"), "name": bt.get("name"), "symbol": bt.get("symbol"),
        "image": info.get("imageUrl"), "links": links, "socials": socials,
        "metrics": {
            "mc": fnum(p.get("marketCap")) or fnum(p.get("fdv")), "fdv": fnum(p.get("fdv")),
            "liq": fnum((p.get("liquidity") or {}).get("usd")), "price": fnum(p.get("priceUsd")),
            "vol": {k: fnum(vol.get(k)) for k in ("m5", "h1", "h6", "h24")},
            "chg": {k: fnum(chg.get(k)) for k in ("m5", "h1", "h6", "h24")},
            "buys_h1": (tx.get("h1") or {}).get("buys"), "sells_h1": (tx.get("h1") or {}).get("sells"),
            "buys_h24": (tx.get("h24") or {}).get("buys"), "sells_h24": (tx.get("h24") or {}).get("sells"),
        },
        "pair": p.get("pairAddress"), "dex": p.get("dexId"),
        "pair_created": p.get("_oldest_pair") or p.get("pairCreatedAt"),
        "boosts_active": (p.get("boosts") or {}).get("active") or 0,
    }

def best_pairs(pairs):
    """Para cada token, el par con más liquidez (solo Solana, token como base)."""
    best, oldest = {}, {}
    for p in pairs or []:
        if p.get("chainId") != "solana":
            continue
        ca = (p.get("baseToken") or {}).get("address")
        if not ca or ca in QUOTE_MINTS:
            continue
        pc = p.get("pairCreatedAt")
        if pc and (ca not in oldest or pc < oldest[ca]):
            oldest[ca] = pc
        liq = fnum((p.get("liquidity") or {}).get("usd")) or 0
        cur = best.get(ca)
        if cur is None or liq > (fnum((cur.get("liquidity") or {}).get("usd")) or 0):
            best[ca] = p
    for ca, p in best.items():
        p["_oldest_pair"] = oldest.get(ca)
    return best

def ds_tokens(addrs):
    """Métricas de DexScreener para una lista de CAs (lotes de 30)."""
    out = {}
    addrs = [a for a in dict.fromkeys(addrs) if a and SOL_ADDR.match(a)]
    for i in range(0, len(addrs), 30):
        chunk = addrs[i:i + 30]
        d = http(f"{DS}/tokens/v1/solana/{','.join(chunk)}", "dexscreener_tokens")
        if isinstance(d, list):
            SRC.mark("dexscreener_tokens", True, len(d))
            out.update(best_pairs(d))
        time.sleep(0.25)
    return out

def src_ds_metas(cfg):
    res = []  # (pair_info, extra_reason)
    for slug, m in cfg["dexscreener_metas"].items():
        d = http(f"{DS}/metas/meta/v1/{slug}", f"dexscreener_meta_{slug}")
        if not isinstance(d, dict):
            continue
        pairs = best_pairs(d.get("pairs"))
        SRC.mark(f"dexscreener_meta_{slug}", True, len(pairs))
        for p in pairs.values():
            res.append((ds_pair_to_info(p), {"t": f"meta_{slug}", "d": m["label"], "w": m["weight"],
                                             "q": bool(m.get("qualifies"))}))
        time.sleep(0.3)
    return res

def src_ds_search(terms):
    found = {}
    for q in terms:
        d = http(f"{DS}/latest/dex/search?q={urllib.parse.quote(q)}", "dexscreener_search")
        if isinstance(d, dict):
            pairs = best_pairs(d.get("pairs"))
            SRC.mark("dexscreener_search", True, len(pairs))
            for ca, p in pairs.items():
                found[ca] = p
        time.sleep(0.6)  # DexScreener limita la búsqueda; mejor ir despacio
    return [ds_pair_to_info(p) for p in found.values()]

def src_ds_profiles():
    """Perfiles/boosts/CTO recientes: dan links y descripción (sin nombre)."""
    cands = {}
    for path, name in (("/token-profiles/latest/v1", "dexscreener_profiles"),
                       ("/token-boosts/latest/v1", "dexscreener_boosts"),
                       ("/token-boosts/top/v1", "dexscreener_boosts_top"),
                       ("/community-takeovers/latest/v1", "dexscreener_cto")):
        d = http(DS + path, name)
        if not isinstance(d, list):
            continue
        n = 0
        for x in d:
            if x.get("chainId") != "solana" or not x.get("tokenAddress"):
                continue
            n += 1
            c = cands.setdefault(x["tokenAddress"], {"desc": "", "links": []})
            c["desc"] = c["desc"] or x.get("description") or ""
            c["links"] += [l.get("url", "") for l in x.get("links") or []]
        SRC.mark(name, True, n)
        time.sleep(0.5)
    return cands

def pump_headers():
    return {"Origin": "https://pump.fun", "Referer": "https://pump.fun/"}

def pump_to_info(c):
    links = [c.get("website") or "", c.get("twitter") or "", c.get("telegram") or ""]
    socials = {}
    if c.get("twitter"): socials["twitter"] = c["twitter"]
    if c.get("telegram"): socials["telegram"] = c["telegram"]
    return {
        "ca": c.get("mint"), "name": c.get("name"), "symbol": c.get("symbol"),
        "desc": c.get("description") or "", "image": c.get("image_uri"),
        "links": [l for l in links if l], "socials": socials, "website": c.get("website"),
        "created": c.get("created_timestamp"), "pump_mc": fnum(c.get("usd_market_cap")),
        "pump_complete": bool(c.get("complete")), "pump_ath": fnum(c.get("ath_market_cap")),
        "dev": c.get("creator"),
    }

def src_pump(state, cfg):
    """Coins nuevas de pump.fun (desde el último visto) + las que más tradean ahora."""
    out = []
    last_ts = state.get("pump_last_created", 0)
    newest = last_ts
    for page in range(cfg["pump_new_max_pages"]):
        d = http(f"{PUMP}/coins?offset={page*50}&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false",
                 "pumpfun_new", headers=pump_headers())
        if not isinstance(d, list) or not d:
            break
        SRC.mark("pumpfun_new", True, len(d))
        out += [pump_to_info(c) for c in d]
        ts = [c.get("created_timestamp") or 0 for c in d]
        newest = max(newest, max(ts))
        if min(ts) <= last_ts:
            break
        time.sleep(1.2)
    state["pump_last_created"] = newest
    for page in range(cfg["pump_active_pages"]):
        time.sleep(1.2)
        d = http(f"{PUMP}/coins?offset={page*50}&limit=50&sort=last_trade_timestamp&order=DESC&includeNsfw=false",
                 "pumpfun_active", headers=pump_headers())
        if not isinstance(d, list) or not d:
            break
        SRC.mark("pumpfun_active", True, len(d))
        out += [pump_to_info(c) for c in d]
    return out

def src_trends(cfg, state):
    """Hashtags en tendencia de TikTok Creative Center (público, sin login: top 3 por país)."""
    cache = state.get("trends_cache") or {}
    age_min = (now_ms() - cache.get("fetched", 0)) / 60000
    if cache.get("items") and age_min < cfg["trends_refresh_minutes"]:
        SRC.status["tiktok_creative_center"] = {"ok": 1, "fail": 0, "items": len(cache["items"]),
                                                "last_error": None, "cached_minutes": round(age_min)}
        return cache["items"]
    items = {}
    hdr = {"agw-js-conv": "str", "Origin": "https://ads.tiktok.com",
           "Referer": "https://ads.tiktok.com/creative/creativeCenter/trends/hashtag",
           "Cookie": "creative_one_locale=en; lang_type=en"}
    for tr in cfg["trend_time_ranges"]:
        for cc in cfg["trend_countries"]:
            d = http("https://ads.tiktok.com/CreativeOne/KnowledgeAPI/GetHashtagList", "tiktok_creative_center",
                     data={"timeRange": tr, "countryCode": cc, "page": 1, "limit": 20}, headers=hdr)
            if not isinstance(d, dict) or (d.get("BaseResp") or {}).get("StatusCode") != 0:
                if isinstance(d, dict):
                    SRC.mark("tiktok_creative_center", False, err=json.dumps(d.get("BaseResp")))
                continue
            SRC.mark("tiktok_creative_center", True, len(d.get("items") or []))
            for it in d.get("items") or []:
                name = it.get("hashtagName")
                if not name:
                    continue
                curve = [fnum(x.get("value")) or 0 for x in it.get("popularityCurve") or []]
                key = norm(name) or name
                e = items.get(key)
                if e is None:
                    e = items[key] = {"name": name, "country": cc, "countries": [], "period_days": tr,
                                      "rank": int(it.get("rankIndex") or 0), "posts": int(it.get("publishCnt") or 0),
                                      "views": int(it.get("vv") or 0), "curve": curve,
                                      "creators": [c.get("handleName") for c in it.get("topCreators") or [] if c.get("handleName")]}
                if cc not in e["countries"]:
                    e["countries"].append(cc)
                if tr < e["period_days"]:
                    e["period_days"] = tr
            time.sleep(0.4)
    lst = list(items.values())
    for e in lst:
        c = e["curve"]
        e["direction"] = ("sube" if len(c) >= 2 and c[-1] >= max(c) * 0.95 and c[-1] > c[0]
                          else "baja" if len(c) >= 2 and c[-1] < max(c) * 0.6 else "estable")
        e["tiktok_url"] = f"https://www.tiktok.com/tag/{urllib.parse.quote(e['name'])}"
    if lst:
        state["trends_cache"] = {"fetched": now_ms(), "items": lst}
        return lst
    return cache.get("items") or []

# ---------------------------------------------------------------- merge
def momentum(m):
    mc = m.get("mc") or 0
    v1 = (m.get("vol") or {}).get("h1") or 0
    c1 = (m.get("chg") or {}).get("h1") or 0
    if mc <= 0:
        return 0.0
    turnover = min(v1 / mc * 100, 300)  # % del MC que se ha movido en 1h
    return round(turnover + max(min(c1, 300), -100) * 0.3, 2)

def upsert(coins, info, reasons, tiktok_links, source, ts):
    ca = info.get("ca")
    if not ca or not SOL_ADDR.match(ca) or ca in QUOTE_MINTS:
        return False
    c = coins.get(ca)
    new = c is None
    if new:
        c = coins[ca] = {"ca": ca, "first_seen": ts, "reasons": [], "sources": [], "links": {}, "metrics": {}}
    for k in ("name", "symbol", "image"):
        if info.get(k):
            c[k] = info[k]
    if info.get("desc"):
        c["desc"] = info["desc"][:300]
    if info.get("created"):
        c["created"] = min(c.get("created") or info["created"], info["created"])
    if info.get("dev") and SOL_ADDR.match(info["dev"]):
        c["dev"] = info["dev"]  # wallet creadora (pump.fun da "creator")
    known = {r["t"] for r in c["reasons"]}
    for r in reasons:
        if r["t"] not in known:
            c["reasons"].append(r); known.add(r["t"])
    if source not in c["sources"]:
        c["sources"].append(source)
    L = c["links"]
    soc = info.get("socials") or {}
    if soc.get("twitter"): L["x"] = soc["twitter"]
    if soc.get("telegram"): L["telegram"] = soc["telegram"]
    for u in info.get("links") or []:
        if u and "tiktok.com" not in u and not any(h in u for h in ("x.com", "twitter.com", "t.me")):
            L.setdefault("website", u)
    if tiktok_links:
        L["tiktok"] = sorted(set((L.get("tiktok") or []) + tiktok_links))[:5]
    if info.get("pump_mc") is not None:
        prev_ath = (c.get("pump") or {}).get("ath") or 0
        c["pump"] = {"mc": info["pump_mc"], "complete": info.get("pump_complete"),
                     "ath": max(info.get("pump_ath") or 0, prev_ath) or None}
    if info.get("metrics"):
        apply_metrics(c, info, ts)
    c["last_match"] = ts
    return new

def apply_metrics(c, info, ts):
    m = info["metrics"]
    c["metrics"] = m
    c["pair"], c["dex"] = info.get("pair"), info.get("dex")
    if info.get("pair_created"):
        c["created"] = min(c.get("created") or info["pair_created"], info["pair_created"])
    if m.get("mc"):
        c["ath_seen"] = max(c.get("ath_seen") or 0, m["mc"])
    c["updated"] = ts
    c["boosts_active"] = info.get("boosts_active") or 0
    if info.get("image") and not c.get("image"):
        c["image"] = info["image"]
    soc = info.get("socials") or {}
    if soc.get("twitter"): c["links"]["x"] = soc["twitter"]

def finalize(c, cfg, ts):
    ca = c["ca"]
    c["links"].update({
        "gmgn": f"https://gmgn.ai/sol/token/{ca}",
        "dexscreener": f"https://dexscreener.com/solana/{ca}",
        "pumpfun": f"https://pump.fun/coin/{ca}",
    })
    m = c.get("metrics") or {}
    if not m.get("mc") and c.get("pump", {}).get("mc"):
        m = c["metrics"] = dict(m, mc=c["pump"]["mc"], from_pump=True)
    # ATH = máximo de: MC más alto visto por el radar en cualquier pasada (ath_seen, persistido en el estado),
    # ath_market_cap de pump.fun (en USD) y el MC actual
    if m.get("mc"):
        c["ath_seen"] = max(c.get("ath_seen") or 0, m["mc"])
    pump_ath = (c.get("pump") or {}).get("ath") or 0
    c["ath"] = max(c.get("ath_seen") or 0, pump_ath, m.get("mc") or 0) or None
    c["ath_src"] = ("pump.fun" if pump_ath and pump_ath >= (c.get("ath_seen") or 0) else "radar") if c["ath"] else None
    c["score"] = sum(r["w"] for r in c["reasons"])
    c["momentum"] = momentum(m)
    liq, mc = m.get("liq"), m.get("mc")
    c["flags"] = []
    if mc and ((liq or 0) < 1000 and mc > 1_000_000 or (liq or 0) < 20000 and mc > 1_000_000_000):
        c["flags"].append("mc_sospechoso")  # MC enorme con liquidez ridícula
    if c.get("pump") and not c["pump"].get("complete") and not m.get("liq"):
        c["flags"].append("bonding_curve")
    dr = cfg["dead_rules"]
    age_h = (ts - (c.get("created") or c["first_seen"])) / 3_600_000
    vol24 = (m.get("vol") or {}).get("h24") or 0
    c["dead"] = bool(age_h > dr["min_age_hours"] and (mc or 0) < dr["max_mc"] and vol24 < dr["max_vol24"])
    # sin actividad: casi sin volumen y MC de recién lanzada (clones muertos al nacer)
    c["inactive"] = bool(age_h > 1 and vol24 < dr["max_vol24"] and (mc or 0) < dr["max_mc"])

def qualifies(reasons):
    """Regla estricta: solo cuenta link de TikTok, tiktok/fyp/douyin en nombre-ticker-descripción
    o la categoría TikTok de DexScreener. Trends, Brainrot y frases 'viral' son solo info extra."""
    return any(r.get("q") for r in reasons)

def too_old(c, cfg, ts):
    created = c.get("created")
    if not created:  # sin fecha de creación conocida: no podemos garantizar < 24 h
        return ts - c["first_seen"] > 15 * 60_000
    return ts - created > cfg["max_age_hours"] * 3_600_000

def prune(coins, cfg, ts):
    keep = {}
    limit_ms = cfg["prune_after_days_dead"] * 86_400_000
    for ca, c in coins.items():
        if too_old(c, cfg, ts) or not qualifies(c["reasons"]):
            continue
        if c.get("dead") and ts - c["first_seen"] > limit_ms and ts - c.get("last_match", 0) > limit_ms:
            continue
        keep[ca] = c
    if len(keep) > cfg["max_coins"]:
        ranked = sorted(keep.values(), key=lambda c: (not c.get("dead"), (c.get("metrics") or {}).get("mc") or 0), reverse=True)
        keep = {c["ca"]: c for c in ranked[:cfg["max_coins"]]}
    return keep

def check_dex_paid(coins, state, cfg, ts):
    """DEX PAID = DexScreener tiene un pedido 'tokenProfile' aprobado para la coin
    (GET /orders/v1/solana/<CA>). Se cachea por CA: si está pagado, ya no se vuelve a consultar."""
    cache = state.setdefault("dexpaid", {})
    recheck = cfg.get("dexpaid_recheck_minutes", 10) * 60_000
    todo = [ca for ca, c in coins.items()
            if not (cache.get(ca) or {}).get("paid") and ts - (cache.get(ca) or {}).get("checked", 0) > recheck]
    todo.sort(key=lambda ca: (cache.get(ca) or {}).get("checked", 0))  # nunca comprobadas primero
    n = 0
    for ca in todo[:cfg.get("dexpaid_max_checks_per_run", 40)]:
        d = http(f"{DS}/orders/v1/solana/{ca}", "dexscreener_orders", retries=2)
        if not isinstance(d, dict):
            continue
        orders = d.get("orders") if isinstance(d.get("orders"), list) else (d if isinstance(d, list) else [])
        prof = [o for o in orders if o.get("type") == "tokenProfile"]
        paid = [o for o in prof if o.get("status") == "approved"]
        e = {"checked": ts, "paid": bool(paid),
             "status": "approved" if paid else (prof[0].get("status") if prof else None)}
        if paid:
            e["paid_at"] = min(o.get("paymentTimestamp") or ts for o in paid)
        boosts = d.get("boosts") if isinstance(d.get("boosts"), list) else []
        e["boost_total"] = sum(b.get("amount") or 0 for b in boosts)
        cache[ca] = e
        n += 1
        time.sleep(0.4)  # límite de DexScreener para /orders: 60 por minuto
    SRC.mark("dexscreener_orders", True, n)
    for ca, c in coins.items():
        e = cache.get(ca) or {}
        c["dex_paid"] = bool(e.get("paid"))
        c["dex_status"] = e.get("status")
        if e.get("paid_at"): c["dex_paid_at"] = e["paid_at"]
        c["boost_total"] = e.get("boost_total") or 0
    # limpiar caché de coins que ya no seguimos (más de 3 días)
    for ca in [ca for ca, e in cache.items() if ca not in coins and ts - e.get("checked", 0) > 3 * 86_400_000]:
        cache.pop(ca, None)

def rematch(coins, M):
    """Re-aplica las reglas actuales a lo ya guardado (por si cambian las reglas)."""
    for c in coins.values():
        L = c.get("links") or {}
        links = list(L.get("tiktok") or []) + [L.get("website") or "", L.get("x") or ""]
        rs, _ = M.match(c.get("name"), c.get("symbol"), c.get("desc"), links)
        metas = [dict(r, q=(r["t"] == "meta_tiktok")) for r in c["reasons"] if r["t"].startswith("meta_")]
        c["reasons"] = metas + rs

# ---------------------------------------------------------------- "No es TikTok" (feedback de Alex) + aprendizaje
def qkeys(reasons):
    """Motivos que hacen entrar la coin (los que cuentan para la regla), como claves estables.
    p.ej. kw_name:tiktok, kw_desc:tiktok, link_tiktok, meta_tiktok."""
    return sorted({r.get("k") or r["t"] for r in reasons or [] if r.get("q")})

def key_label(k):
    if k.startswith("kw_name:"):
        return f"'{k.split(':', 1)[1]}' en nombre/ticker"
    if k.startswith("kw_desc:"):
        return f"'{k.split(':', 1)[1]}' solo en la descripción"
    return {"kw_name": "Palabra TikTok en nombre/ticker", "kw_desc": "Palabra TikTok solo en la descripción",
            "link_tiktok": "Link de TikTok en redes/descripción",
            "meta_tiktok": "Categoría TikTok de DexScreener"}.get(k, k)

def load_feedback(cfg, state):
    """Lee feedback.json de la rama 'feedback' (lo escribe la función de Vercel /api/not-tiktok).
    Si GitHub falla se usa la última copia buena guardada en el estado."""
    L = cfg.get("learning") or {}
    local = os.environ.get("RADAR_FEEDBACK_FILE")  # para pruebas en local
    if local:
        return load_json(local, {}) or {}
    repo = L.get("feedback_repo", "trendtiktokradar/tiktok-radar")
    url = (f"https://api.github.com/repos/{repo}/contents/{L.get('feedback_path', 'feedback.json')}"
           f"?ref={L.get('feedback_branch', 'feedback')}")
    h = {"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN_TIKTOK_RADAR") or os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = "Bearer " + tok  # solo cabecera; nunca se imprime
    d = http(url, "github_feedback", headers=h, retries=2, timeout=15)
    if isinstance(d, dict):
        SRC.mark("github_feedback", True, len(d.get("marks") or {}))
        state["feedback_cache"] = d
        return d
    return state.get("feedback_cache") or {}

class Learner:
    """Coins marcadas 'No es TikTok' (se quitan siempre) + reglas aprendidas:
    si un motivo de entrada acumula >= min_marks marcas de coins que entraron SOLO por ese motivo,
    deja de aceptarse una coin que entre únicamente por él. Nada de IA: solo contar."""
    def __init__(self, fb, cfg):
        L = cfg.get("learning") or {}
        self.enabled = L.get("enabled", True)
        self.min_marks = int(L.get("min_marks", 3))
        self.protected = set(L.get("protected_keys", []))
        self.rules_off = set(fb.get("rules_off") or [])
        self.marked = {ca: m for ca, m in (fb.get("marks") or {}).items()
                       if isinstance(m, dict) and SOL_ADDR.match(ca)}
        self.updated = fb.get("updated")
        groups, seen = {}, {}
        for ca, m in self.marked.items():
            ks = sorted(set(m.get("keys") or []))
            for k in ks:
                seen.setdefault(k, set()).add(m.get("g") or ca)
            if len(ks) == 1:  # solo cuenta como prueba si entró ÚNICAMENTE por ese motivo
                groups.setdefault(ks[0], set()).add(m.get("g") or ca)
        self.counts = {k: len(v) for k, v in groups.items()}
        self.seen = {k: len(v) for k, v in seen.items()}
        self.active = {k for k, n in self.counts.items()
                       if self.enabled and n >= self.min_marks and k not in self.protected and k not in self.rules_off}
        self.blocked = {}        # ca -> info de coins quitadas por regla aprendida en esta pasada
        self.removed_marked = 0  # coins marcadas que han vuelto a aparecer y se han quitado

    def blocks(self, ca, reasons):
        if ca in self.marked:
            return "marked"
        ks = qkeys(reasons)
        if ks and all(k in self.active for k in ks):
            return ks
        return None

    def filter(self, coins):
        out = {}
        for ca, c in coins.items():
            b = self.blocks(ca, c.get("reasons"))
            if b == "marked":
                self.removed_marked += 1
            elif b:
                self.blocked[ca] = {"ca": ca, "name": c.get("name"), "symbol": c.get("symbol"), "keys": b}
            else:
                out[ca] = c
        return out

    def summary(self):
        keys = set(self.seen) | set(self.counts)
        rules = []
        for k in sorted(keys, key=lambda k: (-self.counts.get(k, 0), -self.seen.get(k, 0), k)):
            rules.append({"key": k, "label": key_label(k), "count": self.counts.get(k, 0),
                          "seen": self.seen.get(k, 0), "active": k in self.active,
                          "protected": k in self.protected, "off": k in self.rules_off,
                          "blocked_now": sum(1 for b in self.blocked.values() if k in b["keys"])})
        marked = sorted(({"ca": ca, "name": m.get("name"), "symbol": m.get("symbol"), "keys": m.get("keys") or [],
                          "dev": m.get("dev"), "ts": m.get("ts"), "g": m.get("g")} for ca, m in self.marked.items()),
                        key=lambda x: -(x["ts"] or 0))
        return {"enabled": self.enabled, "min_marks": self.min_marks, "marks_total": len(self.marked),
                "groups_total": len({m.get("g") or ca for ca, m in self.marked.items()}),
                "updated": self.updated, "rules": rules, "marked": marked[:300],
                "blocked_now": list(self.blocked.values())[:100], "removed_marked_now": self.removed_marked,
                "protected_keys": sorted(self.protected)}

# ---------------------------------------------------------------- ATH (pump.fun) 
def refresh_pump_ath(coins, state, cfg, ts):
    """ath_market_cap (USD) de pump.fun /coins-v2/<CA> para todas las coins seguidas, rotando:
    máx. N consultas por pasada, cada coin se refresca cada X min. De paso guarda el dev si faltaba.
    Las CAs que pump.fun no conoce (404) se reintentan pocas veces."""
    A = cfg.get("ath") or {}
    cache = state.setdefault("ath_lookup", {})  # ca -> {"checked": ts, "nopump": bool}
    every = A.get("refresh_minutes", 20) * 60_000
    nopump_every = A.get("nopump_retry_minutes", 360) * 60_000
    def due(ca):
        e = cache.get(ca) or {}
        return ts - e.get("checked", 0) > (nopump_every if e.get("nopump") else every)
    todo = sorted((ca for ca in coins if due(ca)), key=lambda ca: (cache.get(ca) or {}).get("checked", 0))
    n = 0
    for ca in todo[:A.get("max_per_run", 20)]:
        d = http(f"{PUMP}/coins-v2/{ca}", "pumpfun_ath", headers=pump_headers(), retries=2, soft404=True)
        ok = isinstance(d, dict) and d.get("mint") == ca
        cache[ca] = {"checked": ts, "nopump": not ok}
        if ok:
            c = coins[ca]
            p = c.setdefault("pump", {})
            ath, mc = fnum(d.get("ath_market_cap")), fnum(d.get("usd_market_cap"))
            if ath:
                p["ath"] = max(p.get("ath") or 0, ath)
            if mc:
                p["mc"] = mc
            if "complete" in p or ca.endswith("pump"):  # pump.fun también indexa coins de otros launchpads
                p["complete"] = bool(d.get("complete"))
            dev = d.get("creator")
            if dev and SOL_ADDR.match(dev) and not c.get("dev"):
                c["dev"] = dev
                state.setdefault("dev_lookup", {})[ca] = {"dev": dev, "checked": ts}
            n += 1
        time.sleep(0.8)
    SRC.mark("pumpfun_ath", True, n)
    for ca in [ca for ca, e in cache.items() if ca not in coins and ts - e.get("checked", 0) > 2 * 86_400_000]:
        cache.pop(ca, None)

# ---------------------------------------------------------------- dev (wallet creadora) y "TikTok dev 🔥"
def resolve_devs(coins, state, cfg, ts):
    """La wallet creadora viene en las coins de pump.fun; para las demás se pregunta a pump.fun
    /coins-v2/<CA> (también conoce muchas de Meteora/otras). Caché por CA, máx. N consultas por pasada."""
    D = cfg.get("dev_hot") or {}
    cache = state.setdefault("dev_lookup", {})
    for ca, c in coins.items():
        if not c.get("dev") and (cache.get(ca) or {}).get("dev"):
            c["dev"] = cache[ca]["dev"]
    retry = D.get("lookup_retry_minutes", 180) * 60_000
    todo = [ca for ca, c in coins.items() if not c.get("dev") and ts - (cache.get(ca) or {}).get("checked", 0) > retry]
    todo.sort(key=lambda ca: -(coins[ca].get("created") or 0))  # las más nuevas primero
    n = 0
    for ca in todo[:D.get("lookup_max_per_run", 15)]:
        d = http(f"{PUMP}/coins-v2/{ca}", "pumpfun_dev", headers=pump_headers(), retries=2, soft404=True)
        dev = d.get("creator") if isinstance(d, dict) else None
        dev = dev if dev and SOL_ADDR.match(dev) else None
        cache[ca] = {"dev": dev, "checked": ts}
        if dev:
            coins[ca]["dev"] = dev; n += 1
        time.sleep(1.0)
    SRC.mark("pumpfun_dev", True, n)
    for ca in [ca for ca, e in cache.items() if ca not in coins and ts - e.get("checked", 0) > 2 * 86_400_000]:
        cache.pop(ca, None)

def dev_tiktok_keys(rs):
    """Para 'TikTok dev 🔥' solo cuentan señales fuertes: link de TikTok o tiktok/fyp/douyin en nombre/ticker.
    Nunca solo descripción ni solo categoría."""
    return [k for k in qkeys(rs) if k == "link_tiktok" or k.startswith("kw_name")]

def update_dev_history(coins, state, cfg, ts, M, learner):
    """'TikTok dev 🔥' (solo informativo, NO bloquea). Un dev se marca si en los últimos window_days días:
      - tiene >= min_coins coins TikTok con NOMBRE DISTINTO (clones/relanzamientos cuentan 1),
      - >= min_share de TODAS las coins que creó en esos días son TikTok,
      - y su lista completa de pump.fun se puede leer (< max_created coins; si no, es un lanzador masivo).
    Fuente: pump.fun /coins-v2/user-created-coins/<dev> (hasta 5 páginas de 50, caché scan_refresh_minutes)
    + coins del propio radar que no salgan en esa lista."""
    D = cfg.get("dev_hot") or {}
    win = D.get("window_days", 7) * 86_400_000
    th = D.get("min_coins", 3)
    min_share = D.get("min_share", 0.5)
    max_created = D.get("max_created", 250)
    hist = state.setdefault("dev_history", {})  # dev -> {ca: {n, s, t}} (coins TikTok vistas por el radar)
    for c in coins.values():
        if c.get("dev") and dev_tiktok_keys(c.get("reasons")):
            hist.setdefault(c["dev"], {})[c["ca"]] = {"n": c.get("name"), "s": c.get("symbol"),
                                                       "t": c.get("created") or c["first_seen"]}
    prof = state.setdefault("dev_profile", {})  # dev -> {checked, complete, total, all7: {ca: t}, tt7: {ca: {n,s,t}}}
    state.pop("dev_scan", None)  # formato antiguo
    refresh = D.get("scan_refresh_minutes", 60) * 60_000
    devs = sorted({c["dev"] for c in coins.values() if c.get("dev")}, key=lambda d: (prof.get(d) or {}).get("checked", 0))
    n = 0
    for dev in [d for d in devs if ts - (prof.get(d) or {}).get("checked", 0) > refresh][:D.get("scan_max_devs_per_run", 8)]:
        got, total, ok = [], None, True
        for page in range(D.get("scan_max_pages", 5)):
            d = http(f"{PUMP}/coins-v2/user-created-coins/{dev}?offset={page * 50}&limit=50&includeNsfw=true",
                     "pumpfun_dev_coins", headers=pump_headers(), retries=2)
            if not isinstance(d, dict):
                ok = False; break
            total = d.get("count") if isinstance(d.get("count"), int) else total
            got += d.get("coins") or []
            time.sleep(0.8)
            if len(d.get("coins") or []) < 50 or (total is not None and len(got) >= total):
                break
        if not ok and not got:
            continue  # pump.fun falló: se reintenta en la próxima pasada
        n += 1
        got = list({x.get("mint"): x for x in got if x.get("mint")}.values())
        # pump.fun solo deja ver ~250 coins por dev (y en orden no cronológico). Con menos de max_created se ve todo.
        # Con más: se evalúa con las visibles SOLO si entre ellas hay coins de antes de la ventana (la ventana de
        # 7 días queda cubierta); si todas las visibles son de los últimos 7 días, lanza >250/semana = masivo.
        full = ok and total is not None and len(got) >= total and total < max_created
        covered = ok and total is not None and total >= max_created and \
            any(ts - (x.get("created_timestamp") or ts) > win for x in got)
        complete = bool(full or covered)
        all7, tt7 = {}, {}
        for x in got:
            mint, t = x.get("mint") or "", x.get("created_timestamp") or 0
            if not SOL_ADDR.match(mint) or ts - t > win:
                continue
            all7[mint] = t
            rs, _ = M.match(x.get("name"), x.get("symbol"), x.get("description") or "",
                            [x.get("website") or "", x.get("twitter") or ""])
            if dev_tiktok_keys(rs) and not learner.blocks(mint, rs):
                tt7[mint] = {"n": x.get("name"), "s": x.get("symbol"), "t": t}
        prof[dev] = {"checked": ts, "complete": complete, "partial": bool(covered and not full), "total": total,
                     "visible": len(got), "all7": all7, "tt7": tt7}
    SRC.mark("pumpfun_dev_coins", True, n)
    # limpieza
    for dev in list(hist):
        for ca in [ca for ca, e in hist[dev].items() if ts - (e.get("t") or 0) > win or ca in learner.marked]:
            del hist[dev][ca]
        if not hist[dev]:
            del hist[dev]
    for dev in [d for d, p in prof.items() if ts - p.get("checked", 0) > win]:
        prof.pop(dev, None)

    def evaluate(dev):
        p = prof.get(dev)
        if not p or not p.get("complete"):
            return None
        all7 = {ca: t for ca, t in p["all7"].items() if ts - t <= win}
        tt = {ca: e for ca, e in p["tt7"].items() if ts - e["t"] <= win and ca not in learner.marked}
        for ca, e in (hist.get(dev) or {}).items():  # coins del radar que no salen en la lista de pump.fun
            if ca not in all7:
                all7[ca] = e["t"]
            tt.setdefault(ca, e)
        names = {}
        for ca, e in sorted(tt.items(), key=lambda kv: -(kv[1].get("t") or 0)):
            names.setdefault(norm(e.get("n")) or norm(e.get("s")) or ca, dict(e, ca=ca))
        share = len(tt) / len(all7) if all7 else 0
        # la lista de pump.fun NO viene en orden cronológico: las coins que no se ven podrían ser de esta semana.
        # Peor caso: todas las ocultas son de los últimos 7 días y no son TikTok. Tiene que seguir llegando a min_share.
        hidden = max(0, (p.get("total") or 0) - (p.get("visible") or 0)) if p.get("partial") else 0
        share_worst = len(tt) / (len(all7) + hidden) if all7 else 0
        return {"dev": dev, "count": len(names), "tiktok_coins": len(tt), "created7": len(all7),
                "share": round(share, 3), "share_worst": round(share_worst, 3), "hidden": hidden,
                "hot": len(names) >= th and share >= min_share and share_worst >= min_share,
                "partial": bool(p.get("partial")), "total": p.get("total"), "visible": p.get("visible"),
                "coins": [{"ca": e["ca"], "name": e.get("n"), "symbol": e.get("s"), "t": e.get("t")} for e in names.values()]}

    res = {}
    for c in coins.values():
        dev = c.get("dev")
        if dev and dev not in res:
            res[dev] = evaluate(dev)
        r = res.get(dev) if dev else None
        c["dev_hot"] = bool(r and r["hot"])
        c["dev_count"] = r["count"] if r else 0
        if r:
            c["dev_share"], c["dev_created7"] = r["share"], r["created7"]
            c["dev_mass"] = False
        else:
            c.pop("dev_share", None); c.pop("dev_created7", None)
            p = prof.get(dev) if dev else None
            c["dev_mass"] = bool(p and not p.get("complete") and (p.get("total") or 0) >= max_created)
        if c["dev_hot"]:
            c["dev_coins"] = [x for x in r["coins"] if x["ca"] != c["ca"] and norm(x["name"]) != norm(c.get("name"))][:12]
        else:
            c.pop("dev_coins", None)
    hot = sorted((dict(r, coins=r["coins"][:12]) for r in res.values() if r and r["hot"]), key=lambda x: -x["count"])
    return hot[:50]

# ---------------------------------------------------------------- main
def run(no_trends=False):
    t0 = time.time()
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        sys.exit("Falta collector/config.json")
    state = load_json(STATE_PATH, {"coins": {}})
    coins = state.get("coins", {})
    ts = now_ms()

    trends = [] if no_trends else src_trends(cfg, state)
    log(f"trends: {len(trends)}")
    M = Matcher(cfg, trends)
    learner = Learner(load_feedback(cfg, state), cfg)
    log(f"feedback: {len(learner.marked)} marcadas 'No es TikTok', reglas activas: {sorted(learner.active) or '-'}")
    new_count = 0

    # 1) categorías curadas de DexScreener (TikTok / Brainrot)
    for info, extra in src_ds_metas(cfg):
        rs, tl = M.match(info["name"], info["symbol"], "", info["links"])
        if qualifies([extra] + rs):
            new_count += upsert(coins, info, [extra] + rs, tl, "dexscreener_meta", ts)
        elif info["ca"] in coins:  # p.ej. Brainrot: solo añade el badge a coins que ya califican
            upsert(coins, info, [extra], tl, "dexscreener_meta", ts)
    log("metas ok")

    # 2) búsquedas en DexScreener: palabras clave + trends manuales + hashtags de Creative Center
    # (los hashtags de Creative Center ya no se buscan: solo dan badge informativo, no incluyen coins)
    terms = list(cfg["search_terms"]) + list(cfg.get("manual_trends", []))
    for info in src_ds_search(list(dict.fromkeys(terms))):
        rs, tl = M.match(info["name"], info["symbol"], "", info["links"])
        if qualifies(rs):
            new_count += upsert(coins, info, rs, tl, "dexscreener_search", ts)
    log("search ok")

    # 3) perfiles / boosts / CTO recientes de DexScreener
    cands = src_ds_profiles()
    pinfo = ds_tokens(list(cands))
    for ca, cd in cands.items():
        p = pinfo.get(ca)
        info = ds_pair_to_info(p) if p else {"ca": ca, "links": []}
        info["links"] = list(info.get("links") or []) + cd["links"]
        info["desc"] = cd["desc"]
        rs, tl = M.match(info.get("name"), info.get("symbol"), cd["desc"], info["links"])
        if qualifies(rs):
            new_count += upsert(coins, info, rs, tl, "dexscreener_profiles", ts)
    log("profiles ok")

    # 4) pump.fun (nuevas + activas)
    for info in src_pump(state, cfg):
        rs, tl = M.match(info["name"], info["symbol"], info["desc"], info["links"])
        if qualifies(rs):
            new_count += upsert(coins, info, rs, tl, "pumpfun", ts)
    log("pump ok")

    # 5) reglas actuales sobre todo lo guardado + fuera lo que tenga > 24 h
    rematch(coins, M)
    coins = {ca: c for ca, c in coins.items() if not (c.get("created") and too_old(c, cfg, ts))}
    # 5b) fuera las marcadas "No es TikTok" y las que solo entran por un motivo aprendido
    coins = learner.filter(coins)
    # 6) refrescar métricas de TODAS las coins seguidas
    fresh = ds_tokens(list(coins))
    for ca, p in fresh.items():
        if ca in coins:
            apply_metrics(coins[ca], ds_pair_to_info(p), ts)
    refresh_pump_ath(coins, state, cfg, ts)  # ATH real de pump.fun (rotando, con caché)
    for c in coins.values():
        finalize(c, cfg, ts)
    coins = prune(coins, cfg, ts)
    # 7) DEX PAID (con caché por CA)
    check_dex_paid(coins, state, cfg, ts)
    # 8) dev (wallet creadora) + "TikTok dev 🔥" (solo informativo)
    resolve_devs(coins, state, cfg, ts)
    dev_hot = update_dev_history(coins, state, cfg, ts, M, learner)
    state["coins"] = coins
    state["last_run"] = ts
    save_json(STATE_PATH, state)

    # 6) data.json para la web
    tag_hits = {}
    for c in coins.values():
        for r in c["reasons"]:
            if r["t"] == "trend":
                tag_hits[r["d"]] = tag_hits.get(r["d"], 0) + 1
    out_trends = []
    for t in sorted(trends, key=lambda t: (-t["views"])):
        lbl = f"#{t['name']} ({t['country']})"
        out_trends.append(dict(t, coins_matched=tag_hits.get(f"Coincide con trend TikTok {lbl}", 0)))
    lst = sorted(coins.values(), key=lambda c: c.get("created") or c["first_seen"], reverse=True)
    data = {
        "generated_at": iso(), "generated_ms": now_ms(), "run_seconds": round(time.time() - t0, 1),
        "new_this_run": new_count, "total": len(lst), "max_age_hours": cfg["max_age_hours"],
        "dex_paid": sum(1 for c in lst if c.get("dex_paid")), "active": sum(1 for c in lst if not c["dead"]),
        "sources": SRC.status, "trends": out_trends,
        "manual_trends": cfg.get("manual_trends", []),
        "learned": learner.summary(),
        "dev_hot_min": (cfg.get("dev_hot") or {}).get("min_coins", 3),
        "dev_hot_window_days": (cfg.get("dev_hot") or {}).get("window_days", 7),
        "dev_hot_min_share": (cfg.get("dev_hot") or {}).get("min_share", 0.5),
        "dev_hot": dev_hot,
        "coins": lst,
    }
    save_json(DATA_PATH, data, compact=True)
    data["new_this_run"] = sum(1 for c in lst if c["first_seen"] == ts)
    log(f"OK: {len(lst)} coins ({data['active']} activas, {data['new_this_run']} nuevas) en {data['run_seconds']}s")
    for k, v in SRC.status.items():
        log(f"  {k}: ok={v['ok']} fail={v['fail']} items={v['items']} {v.get('last_error') or ''}")
    return data

if __name__ == "__main__":
    run(no_trends="--no-trends" in sys.argv)

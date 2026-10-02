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

def http(url, source, data=None, headers=None, retries=3, timeout=25):
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
                reasons.append({"t": "kw_name", "d": f"'{label}' en nombre/ticker", "w": 3}); break
        else:
            for rx, label in self.strong:
                if rx.search(desc):
                    reasons.append({"t": "kw_desc", "d": f"Menciona '{label}' en la descripción", "w": 2}); break
        tiktok_links = sorted({u.rstrip(".,") for u in TIKTOK_URL.findall(" ".join(links) + " " + desc)})
        if tiktok_links:
            reasons.append({"t": "link_tiktok", "d": "Link de TikTok en sus redes/descripción", "w": 3})
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
        "pair": p.get("pairAddress"), "dex": p.get("dexId"), "pair_created": p.get("pairCreatedAt"),
    }

def best_pairs(pairs):
    """Para cada token, el par con más liquidez (solo Solana, token como base)."""
    best = {}
    for p in pairs or []:
        if p.get("chainId") != "solana":
            continue
        ca = (p.get("baseToken") or {}).get("address")
        if not ca or ca in QUOTE_MINTS:
            continue
        liq = fnum((p.get("liquidity") or {}).get("usd")) or 0
        cur = best.get(ca)
        if cur is None or liq > (fnum((cur.get("liquidity") or {}).get("usd")) or 0):
            best[ca] = p
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
            res.append((ds_pair_to_info(p), {"t": f"meta_{slug}", "d": m["label"], "w": m["weight"]}))
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
        c["created"] = info["created"]
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
        c["pump"] = {"mc": info["pump_mc"], "complete": info.get("pump_complete"), "ath": info.get("pump_ath")}
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
    c["score"] = sum(r["w"] for r in c["reasons"])
    c["momentum"] = momentum(m)
    liq, mc = m.get("liq"), m.get("mc")
    c["flags"] = []
    if liq is not None and mc and liq < 1000 and mc > 1_000_000:
        c["flags"].append("mc_sospechoso")  # MC enorme con liquidez ridícula
    if c.get("pump") and not c["pump"].get("complete") and not m.get("liq"):
        c["flags"].append("bonding_curve")
    dr = cfg["dead_rules"]
    age_h = (ts - (c.get("created") or c["first_seen"])) / 3_600_000
    vol24 = (m.get("vol") or {}).get("h24") or 0
    c["dead"] = bool(age_h > dr["min_age_hours"] and (mc or 0) < dr["max_mc"] and vol24 < dr["max_vol24"])
    # sin actividad: casi sin volumen y MC de recién lanzada (clones muertos al nacer)
    c["inactive"] = bool(vol24 < dr["max_vol24"] and (mc or 0) < dr["max_mc"])

def prune(coins, cfg, ts):
    keep = {}
    limit_ms = cfg["prune_after_days_dead"] * 86_400_000
    for ca, c in coins.items():
        if c["dead"] and ts - c["first_seen"] > limit_ms and ts - c.get("last_match", 0) > limit_ms:
            continue
        keep[ca] = c
    if len(keep) > cfg["max_coins"]:
        ranked = sorted(keep.values(), key=lambda c: (not c["dead"], (c.get("metrics") or {}).get("mc") or 0), reverse=True)
        keep = {c["ca"]: c for c in ranked[:cfg["max_coins"]]}
    return keep

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
    new_count = 0

    # 1) categorías curadas de DexScreener (TikTok / Brainrot)
    for info, extra in src_ds_metas(cfg):
        rs, tl = M.match(info["name"], info["symbol"], "", info["links"])
        new_count += upsert(coins, info, [extra] + rs, tl, "dexscreener_meta", ts)
    log("metas ok")

    # 2) búsquedas en DexScreener: palabras clave + trends manuales + hashtags de Creative Center
    terms = list(cfg["search_terms"]) + list(cfg.get("manual_trends", []))
    # los hashtags cambian poco: se buscan como mucho cada 30 min (DexScreener limita la búsqueda)
    if ts - state.get("last_trend_search", 0) > 30 * 60_000:
        terms += [t["name"] for t in trends if norm(t["name"]) in M.trend_tags]
        state["last_trend_search"] = ts
    for info in src_ds_search(list(dict.fromkeys(terms))):
        rs, tl = M.match(info["name"], info["symbol"], "", info["links"])
        if sum(r["w"] for r in rs) >= cfg["min_score"]:
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
        if sum(r["w"] for r in rs) >= cfg["min_score"]:
            new_count += upsert(coins, info, rs, tl, "dexscreener_profiles", ts)
    log("profiles ok")

    # 4) pump.fun (nuevas + activas)
    for info in src_pump(state, cfg):
        rs, tl = M.match(info["name"], info["symbol"], info["desc"], info["links"])
        if sum(r["w"] for r in rs) >= cfg["min_score"]:
            new_count += upsert(coins, info, rs, tl, "pumpfun", ts)
    log("pump ok")

    # 5) refrescar métricas de TODAS las coins seguidas
    fresh = ds_tokens(list(coins))
    for ca, p in fresh.items():
        if ca in coins:
            apply_metrics(coins[ca], ds_pair_to_info(p), ts)
    for c in coins.values():
        finalize(c, cfg, ts)
    coins = prune(coins, cfg, ts)
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
    lst = sorted(coins.values(), key=lambda c: c["first_seen"], reverse=True)
    data = {
        "generated_at": iso(), "generated_ms": now_ms(), "run_seconds": round(time.time() - t0, 1),
        "new_this_run": new_count, "total": len(lst), "active": sum(1 for c in lst if not c["dead"]),
        "sources": SRC.status, "trends": out_trends,
        "manual_trends": cfg.get("manual_trends", []),
        "coins": lst,
    }
    save_json(DATA_PATH, data, compact=True)
    log(f"OK: {len(lst)} coins ({data['active']} activas, {new_count} nuevas) en {data['run_seconds']}s")
    for k, v in SRC.status.items():
        log(f"  {k}: ok={v['ok']} fail={v['fail']} items={v['items']} {v.get('last_error') or ''}")
    return data

if __name__ == "__main__":
    run(no_trends="--no-trends" in sys.argv)

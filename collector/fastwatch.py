#!/usr/bin/env python3
"""Vigilante rápido de avisos de TikTok Radar (sin IA, solo Python stdlib). Corre en el box junto a loop.sh.

Cada ~45 s (FASTWATCH_INTERVAL), independiente de la pasada lenta (~8 min):
  1. DEX PAID al momento: lee /token-profiles/latest/v1 de DexScreener (cada 20 s, también entre ciclos). Un perfil nuevo (no CTO) = pago
     de DEX. Si la coin tiene link de TikTok asociado (ya seguida por el radar o recién descubierta con /tokens/v1),
     se confirma la hora de pago con /orders y se avisa por Telegram en el acto.
  2. BONDING: /tokens/v1 en lotes de 30 para las coins con link de TikTok aún en bonding curve (el par principal pasa de
     pumpfun/meteoradbc… a un AMM) + pump.fun /coins-v2 (complete=True) para las que están cerca de graduarse.
  3. Respaldo (lo menos urgente, va el último): /orders rotando por las coins con link de TikTok sin pagar
     (máx. orders_per_cycle por ciclo, ~1/s; se pausa mientras la pasada lenta hace sus /orders).
El límite de DexScreener es por IP y común a todos sus endpoints: ante un 429 se espera su Retry-After.
Lee la lista de coins que escribe la pasada lenta (state/state.json), comparte con ella el registro de avisos
enviados (state/telegram.json, con candado de fichero) y deja lo que ve en state/fastwatch.json: la pasada lenta
lo usa para marcar DEX PAID antes (perfiles vistos + resultados de /orders).
Arrancar/parar: scripts/fastwatch.sh start|stop|status|ensure (loop.sh hace "ensure" en cada vuelta).
"""
import fcntl, json, os, re, signal, sys, time, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alerts  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.environ.get("RADAR_STATE", os.path.join(ROOT, "state", "state.json"))
CONFIG_PATH = os.path.join(ROOT, "collector", "config.json")
FW_PATH = os.environ.get("RADAR_FASTWATCH_STATE", os.path.join(ROOT, "state", "fastwatch.json"))
BUSY_PATH = os.path.join(ROOT, "state", "orders_busy")  # la pasada lenta lo toca mientras hace sus /orders
PIDF = os.environ.get("FASTWATCH_PIDFILE", os.path.join(ROOT, "state", "fastwatch.pid"))
DS = "https://api.dexscreener.com"
PUMP = "https://frontend-api-v3.pump.fun"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
CURVE = alerts.CURVE_DEXES
TIKTOK_URL = re.compile(r"(?:https?://)?(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/[^\s\"'<>)]*", re.I)
DAY = 86_400_000

def ms():
    return int(time.time() * 1000)

def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)

def fnum(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None

class Net:
    """GET con contador por endpoint y respeto de 429 (Retry-After). El límite de DexScreener es por IP y común a
    todos sus endpoints (un 429 en uno bloquea todos), así que la espera se aplica a todo DexScreener."""
    def __init__(self, stats):
        self.stats = stats
        self.cool = {}  # grupo ("ds" o "pump") -> ms hasta el que no se llama
    @staticmethod
    def group(key):
        return "pump" if key == "pump" else "ds"
    def cooling(self, key):
        return self.cool.get(self.group(key), 0) > ms()
    def get(self, key, url, headers=None, timeout=15):
        if self.cooling(key):
            return None
        h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
        h.update(headers or {})
        self.stats["req"][key] = self.stats["req"].get(key, 0) + 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.stats["r429"][key] = self.stats["r429"].get(key, 0) + 1
                try:
                    ra = int(e.headers.get("Retry-After") or 30)
                except (TypeError, ValueError):
                    ra = 30
                self.cool[self.group(key)] = ms() + min(max(ra, 5), 120) * 1000
            else:
                self.stats["err"][key] = self.stats["err"].get(key, 0) + 1
        except Exception:
            self.stats["err"][key] = self.stats["err"].get(key, 0) + 1
        return None

def best_pairs(pairs):
    best, oldest = {}, {}
    for p in pairs or []:
        if p.get("chainId") != "solana":
            continue
        ca = (p.get("baseToken") or {}).get("address")
        if not ca:
            continue
        pc = p.get("pairCreatedAt")
        if pc and (ca not in oldest or pc < oldest[ca]):
            oldest[ca] = pc
        liq = fnum((p.get("liquidity") or {}).get("usd")) or 0
        if ca not in best or liq > (fnum((best[ca].get("liquidity") or {}).get("usd")) or 0):
            best[ca] = p
    for ca, p in best.items():
        p["_oldest"] = oldest.get(ca)
    return best

def pair_metrics(p):
    return {"mc": fnum(p.get("marketCap")) or fnum(p.get("fdv")), "liq": fnum((p.get("liquidity") or {}).get("usd"))}

def orders_info(d, now):
    """Mismo criterio que la pasada lenta: pedido 'tokenProfile' aprobado = DEX PAID."""
    orders = d.get("orders") if isinstance(d, dict) and isinstance(d.get("orders"), list) else (d if isinstance(d, list) else [])
    prof = [o for o in orders if o.get("type") == "tokenProfile"]
    paid = [o for o in prof if o.get("status") == "approved"]
    e = {"checked": now, "paid": bool(paid), "status": "approved" if paid else (prof[0].get("status") if prof else None)}
    if paid:
        e["paid_at"] = min(o.get("paymentTimestamp") or now for o in paid)
    boosts = d.get("boosts") if isinstance(d, dict) and isinstance(d.get("boosts"), list) else []
    e["boost_total"] = sum(b.get("amount") or 0 for b in boosts)
    return e

class FastWatch:
    def __init__(self):
        self.fw = self._load(FW_PATH, {})
        for k in ("profiles", "orders", "dex", "pump_checked", "curve_seen", "pump_state", "pump_new"):
            self.fw.setdefault(k, {})
        self.fw.setdefault("alerts", [])
        self.fw["started"] = ms()
        self.fw["stats"] = {"since": ms(), "cycles": 0, "req": {}, "r429": {}, "err": {}, "cycle_s": 0.0}
        self.net = Net(self.fw["stats"])
        self.coins, self.state_mtime, self.state_meta = {}, 0, {}
        self.cfg = {}
        self.tg = alerts.TG(os.environ[alerts.TOKEN_VAR]) if os.environ.get(alerts.TOKEN_VAR) else None
        self.stop = False
        self.last_prof = 0.0

    def maybe_profiles(self):
        """La lista de perfiles nuevos se mira cada profiles_every_s (20 s ≈ 3/min de 60/min), también entre ciclos:
        si DexScreener da 429 se reintenta en cuanto pasa su Retry-After."""
        if time.time() - self.last_prof < self.F("profiles_every_s", 20) or self.net.cooling("profiles"):
            return
        self.last_prof = time.time()
        try:
            self.step_profiles(ms())
        except Exception as ex:
            log(f"perfiles: error {type(ex).__name__}: {str(ex)[:150]}")

    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def save(self):
        tmp = FW_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.fw, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, FW_PATH)

    # ------------------------------------------------------------ datos de la pasada lenta
    def reload(self):
        self.cfg = self._load(CONFIG_PATH, self.cfg) or self.cfg
        try:
            mt = os.path.getmtime(STATE_PATH)
        except OSError:
            return
        if mt == self.state_mtime:
            return
        st = self._load(STATE_PATH, None)
        if not isinstance(st, dict) or "coins" not in st:
            return  # a medio escribir: se reintenta en el siguiente ciclo
        self.state_mtime = mt
        self.coins = st["coins"]
        self.state_meta = {"last_run": st.get("last_run"), "dexpaid": st.get("dexpaid") or {},
                           "marked": set(((st.get("feedback_cache") or {}).get("marks") or {}).keys())}

    def F(self, k, d):
        return (self.cfg.get("fastwatch") or {}).get(k, d)

    def strong(self):
        return [c for c in self.coins.values()
                if not c.get("dead") and c["ca"] not in self.state_meta.get("marked", ()) and alerts.strong_signal(c)]

    def known_paid(self, ca):
        return bool((self.state_meta["dexpaid"].get(ca) or {}).get("paid") or (self.fw["orders"].get(ca) or {}).get("paid"))

    def sent(self, kind, ca):
        return ca in (alerts.load_state().get("sent") or {}).get(kind, {})

    def alert(self, kind, c, event_ts, fresh_at=None):
        if not self.tg:
            return "notoken"
        r = alerts.alert_now(kind, c, self.cfg, event_ts, self.tg, fresh_at)
        now = ms()
        if r == "sent":
            lat = round((now - event_ts) / 1000) if event_ts else None
            self.fw["alerts"] = (self.fw["alerts"] + [{"ca": c["ca"], "name": c.get("name"), "kind": kind,
                                                       "event": event_ts, "sent": now, "latency_s": lat}])[-50:]
            log(f"AVISO {kind}: {c.get('name')} ({c['ca'][:6]}…) latencia {lat if lat is not None else '?'} s")
        elif r == "stale":
            log(f"{kind} antiguo (no se avisa): {c.get('name')} ({c['ca'][:6]}…)")
        elif r not in ("dup", "weak", "off"):
            log(f"aviso {kind} {c['ca'][:6]}…: {r}")
        return r

    # ------------------------------------------------------------ 1) perfiles nuevos = DEX PAID al momento
    def step_profiles(self, now):
        d = self.net.get("profiles", f"{DS}/token-profiles/latest/v1")
        if not isinstance(d, list):
            return
        first = not self.fw.get("profiles_seeded")
        new = []
        for x in d:
            ca = x.get("tokenAddress")
            if x.get("chainId") != "solana" or not ca or x.get("cto"):
                continue
            if ca not in self.fw["profiles"]:
                self.fw["profiles"][ca] = now
                new.append(x)
        if first:  # primer arranque: lo que ya estaba en la lista no se avisa (puede ser antiguo)
            self.fw["profiles_seeded"] = True
            return
        unknown = []
        for x in new:
            ca = x["tokenAddress"]
            c = self.coins.get(ca)
            if c is None:
                unknown.append(x); continue
            if alerts.strong_signal(c) and ca not in self.state_meta["marked"]:
                self.confirm_and_alert(dict(c), now)
        if unknown:
            self.discover(unknown, now)

    def confirm_and_alert(self, c, now):
        ca = c["ca"]
        if self.sent("dex_paid", ca):
            return
        d = self.net.get("orders", f"{DS}/orders/v1/solana/{ca}")
        e = orders_info(d, now) if d is not None else None
        if e and e["paid"]:
            self.fw["orders"][ca] = e
        seen = self.fw["profiles"].get(ca) or now  # el perfil acaba de aparecer = aprobado ahora
        paid_at = (e or {}).get("paid_at") or seen
        c.update(dex_paid=True, dex_paid_at=paid_at)
        self.alert("dex_paid", c, paid_at, fresh_at=seen)

    def discover(self, items, now):
        """Perfil nuevo de una coin que el radar aún no sigue: nombre/MC con /tokens/v1; avisa si tiene link de TikTok
        y tiene menos de max_age_hours (lo mismo que el panel). La pasada lenta la añadirá al panel."""
        max_age = (self.cfg.get("max_age_hours") or 24) * 3_600_000
        cas = [x["tokenAddress"] for x in items][:30]
        d = self.net.get("tokens", f"{DS}/tokens/v1/solana/{','.join(cas)}")
        pairs = best_pairs(d if isinstance(d, list) else [])
        for x in items:
            ca = x["tokenAddress"]
            p = pairs.get(ca)
            if not p:
                continue
            born = p.get("_oldest") or p.get("pairCreatedAt")
            if not born or now - born > max_age or ca in self.state_meta["marked"]:
                continue
            bt = p.get("baseToken") or {}
            info = p.get("info") or {}
            urls = [l.get("url", "") for l in x.get("links") or []] + [w.get("url", "") for w in info.get("websites") or []] \
                + [s.get("url", "") for s in info.get("socials") or []] + [x.get("description") or ""]
            tt = sorted({u for u in urls if "tiktok.com" in (u or "")})
            c = {"ca": ca, "name": bt.get("name"), "symbol": bt.get("symbol"), "metrics": pair_metrics(p),
                 "created": born, "links": {"tiktok": tt, "dexscreener": f"https://dexscreener.com/solana/{ca}",
                                            "pumpfun": f"https://pump.fun/coin/{ca}", "gmgn": f"https://gmgn.ai/sol/token/{ca}"}}
            c["ath"] = c["metrics"].get("mc")
            if alerts.strong_signal(c):
                self.confirm_and_alert(c, now)

    # ------------------------------------------------------------ 2) respaldo: /orders rotando
    def step_orders(self, now, strong):
        try:
            if now - os.path.getmtime(BUSY_PATH) * 1000 < 150_000:
                return  # la pasada lenta está haciendo sus /orders: no pasar de 60/min entre los dos
        except OSError:
            pass
        young_ms = self.F("young_hours", 3) * 3_600_000
        def last(ca):
            return max((self.fw["orders"].get(ca) or {}).get("checked", 0),
                       (self.state_meta["dexpaid"].get(ca) or {}).get("checked", 0))
        todo = []
        for c in strong:
            ca = c["ca"]
            if self.known_paid(ca):
                continue
            young = now - (c.get("created") or c.get("first_seen") or 0) < young_ms
            every = (self.F("young_recheck_min", 5) if young else self.F("old_recheck_min", 20)) * 60_000
            if now - last(ca) >= every:
                todo.append((not young, last(ca), ca))
        todo.sort()
        if not todo:
            return
        sent = alerts.load_state().get("sent", {}).get("dex_paid", {})
        gap = self.F("orders_gap_s", 1.0)
        for _, _, ca in todo[:self.F("orders_per_cycle", 6)]:
            if self.stop:
                break
            d = self.net.get("orders", f"{DS}/orders/v1/solana/{ca}")
            if d is None:
                if self.net.cooling("orders"):
                    break  # 429: se para hasta que DexScreener deje
                continue
            prev = last(ca)  # última comprobación (aún sin pagar)
            e = orders_info(d, ms())
            if e["paid"] and prev:
                e["prev_check"] = prev
            self.fw["orders"][ca] = e
            self.maybe_profiles()
            if e["paid"] and ca not in sent:
                c = dict(self.coins[ca], dex_paid=True, dex_paid_at=e.get("paid_at"))
                self.alert("dex_paid", c, e.get("paid_at"), fresh_at=max(e.get("paid_at") or 0, prev or 0) or None)
            time.sleep(gap)

    # ------------------------------------------------------------ 3) BONDING
    def step_bonding(self, now, strong):
        sent = alerts.load_state().get("sent", {}).get("bonding", {})
        cands = []
        for c in strong:
            ca = c["ca"]
            if ca in sent or (c.get("pump") or {}).get("complete") is True:
                continue
            dex = self.fw["dex"].get(ca) or c.get("dex")
            if dex in CURVE or (not dex and ca.endswith("pump")):
                cands.append(c)
        # cada ciclo: las 150 de más MC (las que pueden graduarse ya) + 90 del resto rotando (~8 lotes de 30)
        cands.sort(key=lambda c: -((c.get("metrics") or {}).get("mc") or 0))
        hot_n, rot_n = self.F("bonding_hot", 120), self.F("bonding_rotate", 60)
        rest = cands[hot_n:]
        if rest:
            self.rot = getattr(self, "rot", 0) % len(rest)
            picked = (rest[self.rot:] + rest[:self.rot])[:rot_n]
            self.rot += rot_n
        else:
            picked = []
        check = cands[:hot_n] + picked
        for i in range(0, len(check), 30):
            if self.stop:
                break
            chunk = check[i:i + 30]
            d = self.net.get("tokens", f"{DS}/tokens/v1/solana/{','.join(c['ca'] for c in chunk)}")
            if not isinstance(d, list):
                if self.net.cooling("tokens"):
                    break
                continue
            pairs = best_pairs(d)
            for c in chunk:
                p = pairs.get(c["ca"])
                if not p or not p.get("dexId"):
                    continue
                ca = c["ca"]
                new = p["dexId"]
                self.fw["dex"][ca] = new
                if new in CURVE:
                    self.fw["curve_seen"].setdefault(ca, now)
                    continue
                seen = [t for t in (c.get("curve_seen"), self.fw["curve_seen"].get(ca)) if t]
                cc = dict(c, dex=new, pair_ts=p.get("pairCreatedAt"), curve_seen=min(seen) if seen else None,
                          metrics=dict(c.get("metrics") or {}, **{k: v for k, v in pair_metrics(p).items() if v}))
                ev = alerts.bonding_event(cc)  # real: pool creado después de verla en curva (o pump.fun complete)
                if ev:
                    self.alert("bonding", cc, ev)
            time.sleep(self.F("tokens_gap_s", 0.6))
        # pump.fun: complete=True para las que están cerca de graduarse (por MC)
        min_mc = self.F("pump_min_mc", 35000)
        near = sorted((c for c in cands if c["ca"].endswith("pump") and ((c.get("metrics") or {}).get("mc") or 0) >= min_mc
                       and c["ca"] not in sent and now - self.fw["pump_checked"].get(c["ca"], 0) > 90_000),
                      key=lambda c: -((c.get("metrics") or {}).get("mc") or 0))
        for c in near[:self.F("pump_per_cycle", 8)]:
            if self.stop:
                break
            d = self.net.get("pump", f"{PUMP}/coins-v2/{c['ca']}",
                             headers={"Origin": "https://pump.fun", "Referer": "https://pump.fun/"})
            self.fw["pump_checked"][c["ca"]] = now
            if isinstance(d, dict) and d.get("mint") == c["ca"]:
                was = self.fw["pump_state"].get(c["ca"])
                self.fw["pump_state"][c["ca"]] = bool(d.get("complete"))
                if d.get("complete") is True and was is False:  # visto pasar de curva a completa: graduación ahora
                    cc = dict(c, pump=dict(c.get("pump") or {}, complete=True))
                    self.alert("bonding", cc, now)
            time.sleep(0.8)

    # ------------------------------------------------------------ 4) graduaciones instantáneas en pump.fun
    def step_pump_new(self, now):
        """pump.fun /coins?complete=true&sort=created_timestamp: coins recién creadas que YA completaron la bonding
        curve (lanzamientos que se gradúan en minutos; el radar no llega a verlas en curva). Si tienen link de TikTok
        en website/twitter/telegram/descripción y se crearon hace ≤ fresh_minutes, aviso BONDING al momento."""
        fresh = ((self.cfg.get("alerts") or {}).get("fresh_minutes", 30) or 30) * 60_000
        d = self.net.get("pump", f"{PUMP}/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&complete=true"
                                 "&includeNsfw=false", headers={"Origin": "https://pump.fun", "Referer": "https://pump.fun/"})
        if not isinstance(d, list):
            return
        hits = []
        for x in d:
            ca, created = x.get("mint"), x.get("created_timestamp") or 0
            if not ca or x.get("complete") is not True or now - created > fresh or ca in self.fw["pump_new"]:
                continue
            self.fw["pump_new"][ca] = now
            text = " ".join(str(x.get(k) or "") for k in ("website", "twitter", "telegram", "description"))
            tt = sorted({u.rstrip(".,") for u in TIKTOK_URL.findall(text)})
            if not tt or ca in self.state_meta.get("marked", ()):
                continue
            c = dict(self.coins.get(ca) or {})
            c.update({"ca": ca, "name": c.get("name") or x.get("name"), "symbol": c.get("symbol") or x.get("symbol"),
                      "created": created, "pump": {"complete": True, "ath": fnum(x.get("ath_market_cap"))}})
            c["links"] = dict(c.get("links") or {}, tiktok=sorted(set(((c.get("links") or {}).get("tiktok") or []) + tt)),
                              dexscreener=f"https://dexscreener.com/solana/{ca}", pumpfun=f"https://pump.fun/coin/{ca}",
                              gmgn=f"https://gmgn.ai/sol/token/{ca}")
            if alerts.tiktok_link(c):
                hits.append(c)
        if not hits:
            return
        # MC y hora del pool (graduación) desde DexScreener si ya lo tiene
        dd = self.net.get("tokens", f"{DS}/tokens/v1/solana/{','.join(c['ca'] for c in hits[:30])}")
        pairs = best_pairs(dd if isinstance(dd, list) else [])
        for c in hits:
            p = pairs.get(c["ca"])
            ev = alerts.bonding_event(c) or now  # hora del pool si el radar ya la tenía; si no, ahora
            if p:
                c["metrics"] = dict(c.get("metrics") or {}, **{k: v for k, v in pair_metrics(p).items() if v})
                if p.get("dexId") not in CURVE and p.get("pairCreatedAt"):
                    ev = p["pairCreatedAt"]
            c["ath"] = max(c.get("ath") or 0, (c.get("metrics") or {}).get("mc") or 0, c["pump"].get("ath") or 0) or None
            self.alert("bonding", c, ev, fresh_at=max(ev, c["created"]))

    # ------------------------------------------------------------ ciclo
    def cleanup(self, now):
        for k in ("profiles", "pump_checked", "curve_seen", "pump_new"):
            for ca in [ca for ca, t in self.fw[k].items() if now - t > 3 * DAY]:
                self.fw[k].pop(ca, None)
        for ca in [ca for ca in self.fw["pump_state"] if ca not in self.coins]:
            self.fw["pump_state"].pop(ca, None)
        for ca in [ca for ca, e in self.fw["orders"].items() if now - e.get("checked", 0) > 3 * DAY]:
            self.fw["orders"].pop(ca, None)
        for ca in [ca for ca in self.fw["dex"] if ca not in self.coins]:
            self.fw["dex"].pop(ca, None)

    def cycle(self):
        t0 = time.time()
        now = ms()
        self.reload()
        A, kinds = alerts.enabled_kinds(self.cfg)
        if self.tg and kinds:
            meta = self.state_meta
            data = {"generated_ms": meta.get("last_run"), "total": len(self.coins),
                    "max_age_hours": self.cfg.get("max_age_hours", 24),
                    "dex_paid": sum(1 for ca in self.coins if (meta.get("dexpaid", {}).get(ca) or {}).get("paid"))}
            try:
                alerts.poll_commands(data, self.cfg, self.tg)
            except Exception as ex:
                log("comandos: error", type(ex).__name__)
        strong = self.strong() if self.coins else []
        self.maybe_profiles()
        # orden por prioridad (el cupo de DexScreener por IP es pequeño): perfiles > bonding (30 coins por petición) > /orders
        for name, fn in (("pump_new", lambda: self.step_pump_new(now)), ("bonding", lambda: self.step_bonding(now, strong)),
                         ("orders", lambda: self.step_orders(now, strong))):
            if self.stop:
                break
            self.maybe_profiles()
            try:
                fn()
            except Exception as ex:  # un fallo en un paso no tumba el vigilante
                log(f"{name}: error {type(ex).__name__}: {str(ex)[:150]}")
        self.cleanup(now)
        s = self.fw["stats"]
        s["cycles"] += 1
        s["cycle_s"] = round(time.time() - t0, 1)
        self.fw["heartbeat"] = ms()
        self.save()
        if s["cycles"] == 1 or s["cycles"] % self.F("status_every", 40) == 0:
            mins = max((ms() - s["since"]) / 60000, 1.0)
            rate = " ".join(f"{k}={v / mins:.1f}/min" for k, v in sorted(s["req"].items()))
            log(f"ciclo {s['cycles']} ({s['cycle_s']} s): {len(strong)} coins con link de TikTok · peticiones {rate} · "
                f"429={s['r429'] or 0} · errores={s['err'] or 0} · avisos={len(self.fw['alerts'])}")

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stop", True))
        log(f"vigilante rápido arrancado (pid {os.getpid()}, cada {self.F('interval_s', 45)} s)"
            + ("" if self.tg else " · SIN token de Telegram: solo detecta, no avisa"))
        while not self.stop:
            t0 = time.time()
            try:
                self.cycle()
            except Exception as ex:
                log("ciclo: error", type(ex).__name__, str(ex)[:150])
            wait = float(os.environ.get("FASTWATCH_INTERVAL") or self.F("interval_s", 45)) - (time.time() - t0)
            end = time.time() + max(wait, 5)
            while not self.stop and time.time() < end:
                time.sleep(1)
                n = len(self.fw["profiles"])
                self.maybe_profiles()
                if len(self.fw["profiles"]) != n:
                    self.save()
        self.save()
        log("vigilante rápido parado")

def main():
    os.makedirs(os.path.dirname(FW_PATH), exist_ok=True)
    lockf = open(FW_PATH + ".run.lock", "a")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)  # una sola instancia
    except OSError:
        log("ya hay otro vigilante rápido en marcha; salgo"); return
    with open(PIDF, "w") as f:
        f.write(str(os.getpid()))
    fw = FastWatch()
    if "--once" in sys.argv:
        fw.cycle(); log(json.dumps(fw.fw["stats"])); return
    fw.run()

if __name__ == "__main__":
    main()

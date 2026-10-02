(() => {
  "use strict";
  const CFG = Object.assign({ dataUrl: "data.json", refreshSeconds: 120 }, window.RADAR_CONFIG || {});
  const $ = (s) => document.querySelector(s);
  const LS_VISIT = "ttr_last_visit", LS_FILTERS = "ttr_filters_v2", LS_HIDDEN = "ttr_hidden";
  // coins ocultadas a mano: { CA: timestamp } guardado en este navegador
  let HIDDEN = {};
  try { HIDDEN = JSON.parse(localStorage.getItem(LS_HIDDEN) || "{}") || {}; } catch (_) { HIDDEN = {}; }
  const saveHidden = () => localStorage.setItem(LS_HIDDEN, JSON.stringify(HIDDEN));
  // "No es TikTok": se oculta al momento aquí (localStorage) y se guarda para todos vía /api/not-tiktok
  const LS_NTT = "ttr_nottiktok", LS_PIN = "ttr_pin", API = CFG.apiUrl || "/api/not-tiktok";
  let NTT = {};
  try { NTT = JSON.parse(localStorage.getItem(LS_NTT) || "{}") || {}; } catch (_) { NTT = {}; }
  const saveNtt = () => localStorage.setItem(LS_NTT, JSON.stringify(NTT));
  const SERVER = { configured: null, cas: new Set(), rules_off: [] };
  const isNotTT = (ca) => !!NTT[ca] || SERVER.cas.has(ca);
  let BYCA = new Map();
  let DATA = null, trendFilter = null;
  // "última visita" = momento en que abriste el panel la vez anterior
  const prevVisit = Number(localStorage.getItem(LS_VISIT) || 0);
  localStorage.setItem(LS_VISIT, String(Date.now()));

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : null);
  const money = (v) => v == null || isNaN(v) ? "–" : v >= 1e9 ? "$" + (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M" : v >= 1e3 ? "$" + (v / 1e3).toFixed(1) + "K" : "$" + Math.round(v);
  const num = (v) => v == null ? "–" : v >= 1e9 ? (v / 1e9).toFixed(1) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(v);
  const pct = (v) => v == null ? "<span>–</span>" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : ""}${Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("es") : v.toFixed(1)}%</span>`;
  const ago = (ms) => {
    if (!ms) return "–";
    const m = Math.max(0, (Date.now() - ms) / 60000);
    if (m < 60) return Math.round(m) + " min";
    if (m < 1440) return (m / 60).toFixed(m < 600 ? 1 : 0) + " h";
    return Math.round(m / 1440) + " d";
  };
  const fmtTime = (ms) => new Date(ms).toLocaleString("es-ES", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" });
  let toastTimer = null;
  const toast = (t, action, ms) => {
    const el = $("#toast"); el.textContent = t; el.classList.toggle("act", !!action);
    if (action) { const b = document.createElement("button"); b.textContent = action.label; b.onclick = () => { el.classList.remove("show"); action.fn(); }; el.appendChild(b); }
    el.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), ms || (action ? 6000 : 1600));
  };
  const shortAddr = (a) => a ? a.slice(0, 4) + "…" + a.slice(-4) : "";
  const normName = (s) => String(s || "").toLowerCase().replace(/[^0-9a-z\u00c0-\uffff]/g, "");

  // motivos que hacen entrar una coin (regla estricta) vs. badges solo informativos
  const reasonCls = (r) => r.q ? "direct" : "info";
  const qkeys = (c) => [...new Set((c.reasons || []).filter((r) => r.q).map((r) => r.k || r.t))].sort();
  const reasonShort = (r) => ({ kw_name: "TikTok en nombre", kw_desc: "TikTok en descripción", link_tiktok: "Link TikTok", meta_tiktok: "Cat. TikTok (DexS)", meta_brainrot: "Brainrot (DexS)", phrase: "Frase viral" }[r.t] || (r.t === "trend" ? (r.d.match(/#\S+/) || ["Trend"])[0] : r.t === "manual" ? "Trend manual" : r.t));

  // ---------- carga de datos
  async function fetchJson(url) {
    const sep = url.includes("?") ? "&" : "?";
    const r = await fetch(url + sep + "t=" + Math.floor(Date.now() / 60000), { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  // marcas guardadas en GitHub (vía la función de Vercel): así se ocultan al momento en todos los dispositivos
  async function loadServer() {
    try {
      const r = await fetch(API, { cache: "no-store" });
      if (!r.ok) { SERVER.configured = r.status === 404 ? "noapi" : false; return; }
      const j = await r.json();
      SERVER.configured = !!j.configured;
      if (j.configured) { SERVER.cas = new Set(j.cas || []); SERVER.rules_off = j.rules_off || []; }
    } catch (_) { SERVER.configured = "noapi"; }
  }
  async function load() {
    loadServer().then(() => DATA && (renderCoins(), renderLearned()));
    const urls = [CFG.dataUrl].concat(CFG.fallbackUrl && CFG.fallbackUrl !== CFG.dataUrl ? [CFG.fallbackUrl] : []);
    let err = null;
    for (const u of urls) {
      try {
        const d = await fetchJson(u);
        // si la copia de respaldo es más vieja que lo que ya tenemos, no la usamos
        if (DATA && d.generated_ms < DATA.generated_ms) return;
        DATA = d; BYCA = new Map(DATA.coins.map((c) => [c.ca, c])); render(); return;
      } catch (e) { err = e; }
    }
    $("#meta").innerHTML = `<span class="stale">No se pudo cargar data.json (${esc(err && err.message)}).</span>`;
  }

  function renderMeta() {
    const g = DATA.generated_ms || Date.parse(DATA.generated_at);
    const mins = (Date.now() - g) / 60000;
    const newSince = prevVisit ? DATA.coins.filter((c) => c.first_seen > prevVisit).length : 0;
    $("#meta").innerHTML = `Última actualización: <b>${fmtTime(g)}</b> <span class="${mins > 30 ? "stale" : ""}">(hace ${ago(g)})</span> · ${DATA.total} coins TikTok &lt; ${DATA.max_age_hours || 24} h · ${DATA.dex_paid ?? 0} DEX PAID` +
      (prevVisit ? ` · <b>${newSince}</b> nuevas desde tu última visita` : "");
  }

  // ---------- filtros
  const F = () => ({
    q: $("#q").value.trim().toLowerCase(), sort: $("#sort").value,
    mcmin: parseFloat($("#mcmin").value) || 0, mcmax: parseFloat($("#mcmax").value) || Infinity,
    agemax: parseFloat($("#agemax").value) || Infinity, liqmin: parseFloat($("#liqmin").value) || 0,
    hideInactive: $("#hideInactive").checked, group: $("#groupClones").checked, onlyNew: $("#onlyNew").checked,
    onlyPaid: $("#onlyPaid").checked, showHidden: $("#showHidden").checked, onlyDevHot: $("#onlyDevHot").checked,
  });
  const saveFilters = () => { const f = F(); delete f.q; localStorage.setItem(LS_FILTERS, JSON.stringify(f)); };
  function restoreFilters() {
    try {
      const f = JSON.parse(localStorage.getItem(LS_FILTERS) || "null"); if (!f) return;
      $("#sort").value = f.sort || "young";
      if (f.mcmin) $("#mcmin").value = f.mcmin; if (f.mcmax !== null && isFinite(f.mcmax)) $("#mcmax").value = f.mcmax;
      if (f.agemax !== null && isFinite(f.agemax)) $("#agemax").value = f.agemax; if (f.liqmin) $("#liqmin").value = f.liqmin;
      $("#hideInactive").checked = f.hideInactive !== false; $("#groupClones").checked = f.group !== false; $("#onlyNew").checked = !!f.onlyNew;
      $("#onlyPaid").checked = !!f.onlyPaid; $("#onlyDevHot").checked = !!f.onlyDevHot;
    } catch (_) {}
  }

  const sortKey = {
    new: (c) => c.first_seen + (c.created || 0) / 1e13, young: (c) => c.created || c.first_seen, mc: (c) => c.metrics?.mc || 0, vol1: (c) => c.metrics?.vol?.h1 || 0,
    vol24: (c) => c.metrics?.vol?.h24 || 0, mom: (c) => c.momentum || 0, chg1: (c) => c.metrics?.chg?.h1 ?? -1e9, score: (c) => c.score * 1e12 + (c.metrics?.mc || 0),
  };

  function filtered() {
    const f = F(), now = Date.now();
    let out = DATA.coins.filter((c) => {
      const m = c.metrics || {}, mc = m.mc || 0;
      if (f.showHidden ? !HIDDEN[c.ca] : !!HIDDEN[c.ca]) return false;
      if (isNotTT(c.ca)) return false;
      if (f.onlyPaid && !c.dex_paid) return false;
      if (f.onlyDevHot && !c.dev_hot) return false;
      if (f.hideInactive && !f.showHidden && (c.inactive || c.dead)) return false;
      if (f.onlyNew && !(prevVisit && c.first_seen > prevVisit)) return false;
      if (mc < f.mcmin || mc > f.mcmax) return false;
      if ((m.liq || 0) < f.liqmin) return false;
      const created = c.created || c.first_seen;
      if ((now - created) / 3.6e6 > f.agemax) return false;
      if (trendFilter && !c.reasons.some((r) => r.t === "trend" && r.d.includes("#" + trendFilter + " "))) return false;
      if (f.q) {
        const hay = [c.name, c.symbol, c.ca, c.desc, ...c.reasons.map((r) => r.d)].join(" ").toLowerCase();
        if (!hay.includes(f.q)) return false;
      }
      return true;
    });
    // agrupar clones: mismo nombre normalizado o mismo ticker+nombre → se queda la de más MC
    let groups = out.map((c) => ({ main: c, clones: [] }));
    if (f.group) {
      const byKey = new Map();
      for (const c of out) {
        const k = normName(c.name) || normName(c.symbol);
        if (!byKey.has(k)) byKey.set(k, []);
        byKey.get(k).push(c);
      }
      groups = [...byKey.values()].map((arr) => {
        arr.sort((a, b) => (b.metrics?.mc || 0) - (a.metrics?.mc || 0));
        return { main: arr[0], clones: arr.slice(1) };
      });
    }
    const key = sortKey[f.sort] || sortKey.young;
    groups.sort((a, b) => {
      const newest = f.sort === "new" || f.sort === "young";
      const ka = newest ? Math.max(key(a.main), ...a.clones.map(key)) : key(a.main);
      const kb = newest ? Math.max(key(b.main), ...b.clones.map(key)) : key(b.main);
      return kb - ka;
    });
    return groups;
  }

  // ---------- render coins
  function card(g) {
    const c = g.main, m = c.metrics || {}, L = c.links || {}, v = m.vol || {}, ch = m.chg || {};
    const isNew = prevVisit && (c.first_seen > prevVisit || g.clones.some((x) => x.first_seen > prevVisit));
    const img = safeUrl(c.image) ? `<img loading="lazy" src="${safeUrl(c.image)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'ph'}))">` : `<div class="ph"></div>`;
    const badges = [];
    if (isNew) badges.push(`<span class="b new">NUEVA</span>`);
    if (c.dex_paid) badges.push(`<span class="b paid" title="Perfil de DexScreener pagado${c.dex_paid_at ? " · " + esc(fmtTime(c.dex_paid_at)) : ""}"><img src="img/dexscreener.png" alt="" width="13" height="13">DEX PAID</span>`);
    else if (c.dex_status === "processing" || c.dex_status === "on-hold") badges.push(`<span class="b pending" title="Pedido de perfil en DexScreener aún sin aprobar">DEX en revisión</span>`);
    if (c.dev_hot) {
      const others = (c.dev_coins || []).map((x) => (x.name || "?") + " ($" + (x.symbol || "?") + ")").join(", ");
      badges.push(`<span class="b devhot" data-devca="${esc(c.ca)}" title="El dev ha creado ${esc(c.dev_count)} coins TikTok en ${esc(DATA.dev_hot_window_days || 7)} días (contando esta): ${esc(others)}">TikTok dev 🔥 (${esc(c.dev_count)})</span>`);
    }
    if (c.boosts_active) badges.push(`<span class="b boost" title="Boosts activos en DexScreener">⚡ ${esc(c.boosts_active)} boosts</span>`);
    for (const r of [...c.reasons].sort((a, b) => (b.q ? 1 : 0) - (a.q ? 1 : 0))) badges.push(`<span class="b ${reasonCls(r)}" title="${esc(r.d)}${r.q ? "" : " (solo info, no cuenta para entrar)"}">${esc(reasonShort(r))}</span>`);
    if ((c.flags || []).includes("mc_sospechoso")) badges.push(`<span class="b warn" title="MC muy alto con liquidez casi nula">MC dudoso</span>`);
    if ((c.flags || []).includes("bonding_curve")) badges.push(`<span class="b" title="Aún en la bonding curve de pump.fun">Bonding curve</span>`);
    if (c.pump?.complete) badges.push(`<span class="b" title="Graduada de pump.fun">Graduada</span>`);
    if (g.clones.length) badges.push(`<span class="b clones" data-ca="${esc(c.ca)}">+${g.clones.length} clones</span>`);
    const link = (u, t, cls = "") => safeUrl(u) ? `<a class="${cls}" href="${safeUrl(u)}" target="_blank" rel="noopener noreferrer">${t}</a>` : "";
    const tts = (L.tiktok || []).map((u, i) => link(u, "TikTok" + (L.tiktok.length > 1 ? " " + (i + 1) : ""), "tt")).join("");
    const clones = g.clones.length ? `<div class="clist" id="cl-${esc(c.ca)}" hidden>${g.clones.map((x) =>
      `<div><span>${esc(x.symbol)} · ${money(x.metrics?.mc)} · hace ${ago(x.created || x.first_seen)}</span><span><a href="${safeUrl(x.links?.gmgn) || "#"}" target="_blank" rel="noopener">GMGN</a> · <a href="#" data-copy="${esc(x.ca)}">copiar CA</a></span></div>`).join("")}</div>` : "";
    const devList = c.dev_hot ? `<div class="clist devlist" id="dv-${esc(c.ca)}" hidden><div><span>Otras coins TikTok de este dev (${esc(shortAddr(c.dev))}):</span><span>${link("https://gmgn.ai/sol/address/" + c.dev, "GMGN dev")} · ${link("https://pump.fun/profile/" + c.dev, "pump.fun")}</span></div>${(c.dev_coins || []).map((x) =>
      `<div><span>${esc(x.name || "?")} ($${esc(x.symbol || "?")}) · hace ${ago(x.t)}</span><span><a href="https://gmgn.ai/sol/token/${esc(x.ca)}" target="_blank" rel="noopener">GMGN</a> · <a href="#" data-copy="${esc(x.ca)}">copiar CA</a></span></div>`).join("")}</div>` : "";
    const groupCas = esc([c.ca, ...g.clones.map((x) => x.ca)].join(","));
    return `<article class="card${isNew ? " new" : ""}${c.inactive ? " inactive" : ""}">
      <div class="head">${img}<div class="ttl"><div class="nm">${esc(c.name || "?")}</div><div class="sym">$${esc(c.symbol || "?")} · score ${c.score}</div></div>
        <div class="age">edad ${ago(c.created || c.first_seen)}<br><span title="Primera vez visto por el radar">visto hace ${ago(c.first_seen)}</span></div></div>
      <div class="badges">${badges.join("")}</div>
      <div class="stats">
        <div class="stat"><div class="k">MC</div><div class="v">${money(m.mc)}</div></div>
        <div class="stat"><div class="k">Liq</div><div class="v">${money(m.liq)}</div></div>
        <div class="stat"><div class="k">Vol 1h</div><div class="v">${money(v.h1)}</div></div>
        <div class="stat"><div class="k">Vol 24h</div><div class="v">${money(v.h24)}</div></div>
        <div class="stat"><div class="k">Δ 5m</div><div class="v">${pct(ch.m5)}</div></div>
        <div class="stat"><div class="k">Δ 1h</div><div class="v">${pct(ch.h1)}</div></div>
        <div class="stat"><div class="k">Δ 24h</div><div class="v">${pct(ch.h24)}</div></div>
        <div class="stat"><div class="k">Momentum</div><div class="v">${c.momentum ?? "–"}</div></div>
      </div>
      ${c.desc ? `<div class="desc">${esc(c.desc)}</div>` : ""}
      <div class="ca"><code>${esc(c.ca)}</code><button data-copy="${esc(c.ca)}">Copiar CA</button></div>
      <div class="links">${link(L.gmgn, "GMGN")}${link(L.dexscreener, "DexScreener")}${link(L.pumpfun, "pump.fun")}${link(L.x, "X")}${tts}${link(L.website, "Web")}${link(L.telegram, "TG")}${c.dev ? link("https://pump.fun/profile/" + c.dev, "Dev " + esc(shortAddr(c.dev))) : ""}</div>
      ${clones}${devList}
      <div class="cardbar"><button class="nttbtn" data-ntt="${groupCas}" title="Quitar del radar en todos tus dispositivos y enseñar al filtro">🚫 No es TikTok${g.clones.length ? ` (+${g.clones.length})` : ""}</button><button class="hidebtn" data-hide="${groupCas}">${HIDDEN[c.ca] ? "Mostrar de nuevo" : "Ocultar" + (g.clones.length ? ` (+${g.clones.length} clones)` : "")}</button></div>
    </article>`;
  }

  function renderCoins() {
    const groups = filtered();
    const n = groups.reduce((a, g) => a + 1 + g.clones.length, 0);
    const nh = Object.keys(HIDDEN).length;
    $("#hiddenCount").textContent = nh ? `(${nh})` : "";
    $("#summary").innerHTML = (F().showHidden ? "Viendo coins OCULTAS · " : "") + `${groups.length} resultados (${n} coins) · solo coins creadas en las últimas ${DATA.max_age_hours || 24} h` + (trendFilter ? ` · filtrando trend <b>#${esc(trendFilter)}</b> <button class="link" id="clearTrend">quitar</button>` : "");
    $("#list").innerHTML = groups.length ? groups.slice(0, 400).map(card).join("") : `<div class="empty">No hay coins con estos filtros.</div>`;
    const ct = $("#clearTrend"); if (ct) ct.onclick = () => { trendFilter = null; renderCoins(); };
  }

  // ---------- trends
  function spark(curve) {
    if (!curve || curve.length < 2) return "";
    const max = Math.max(...curve, 1), w = 70, h = 20;
    const pts = curve.map((v, i) => `${(i / (curve.length - 1)) * w},${h - (v / max) * h}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h + 1}"><polyline fill="none" stroke="#25f4ee" stroke-width="1.5" points="${pts}"/></svg>`;
  }
  function renderTrends() {
    const t = DATA.trends || [];
    $("#trends").innerHTML = t.length ? `<table><thead><tr><th>Hashtag</th><th class="hide-m">Países</th><th class="num">Views</th><th class="num hide-m">Posts</th><th>Tendencia</th><th class="num">Coins</th></tr></thead><tbody>${t.map((x) =>
      `<tr><td><a href="${safeUrl(x.tiktok_url)}" target="_blank" rel="noopener">#${esc(x.name)}</a></td><td class="hide-m">${esc((x.countries || [x.country]).join(", "))}</td><td class="num">${num(x.views)}</td><td class="num hide-m">${num(x.posts)}</td>
      <td>${spark(x.curve)} <span class="dir-${esc(x.direction)}">${{ sube: "▲ sube", baja: "▼ baja", estable: "■ estable" }[x.direction] || ""}</span></td>
      <td class="num">${x.coins_matched ? `<button class="link" data-trend="${esc(x.name)}">ver ${x.coins_matched}</button>` : "0"}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">Creative Center no ha devuelto trends en esta pasada.</div>`;
    const mt = DATA.manual_trends || [];
    $("#manual").innerHTML = mt.length ? mt.map((x) => `<span class="b trend">${esc(x)}</span>`).join(" ") : `<span class="note">Ninguno todavía.</span>`;
  }

  function renderSources() {
    const names = { tiktok_creative_center: "TikTok Creative Center (hashtags)", dexscreener_meta_tiktok: "DexScreener · categoría TikTok", dexscreener_meta_brainrot: "DexScreener · categoría Brainrot", dexscreener_search: "DexScreener · búsqueda", dexscreener_profiles: "DexScreener · perfiles nuevos", dexscreener_boosts: "DexScreener · boosts", dexscreener_boosts_top: "DexScreener · top boosts", dexscreener_cto: "DexScreener · CTOs", dexscreener_tokens: "DexScreener · métricas", dexscreener_orders: "DexScreener · DEX PAID (orders)", pumpfun_new: "pump.fun · coins nuevas", pumpfun_active: "pump.fun · coins activas", pumpfun_dev: "pump.fun · dev (wallet creadora)", pumpfun_dev_coins: "pump.fun · coins creadas por el dev", github_feedback: "GitHub · marcas 'No es TikTok'" };
    const s = DATA.sources || {};
    $("#sources").innerHTML = `<table><thead><tr><th>Fuente</th><th>Estado</th><th class="num">Items</th><th class="hide-m">Error</th></tr></thead><tbody>${Object.entries(s).map(([k, v]) =>
      `<tr><td>${esc(names[k] || k)}</td><td>${v.ok && !v.fail ? "🟢 OK" : v.ok ? "🟡 parcial" : "🔴 falla"}</td><td class="num">${v.items}</td><td class="hide-m note">${esc(v.last_error || "")}</td></tr>`).join("")}</tbody></table>
      <p class="note">Pasada: ${DATA.run_seconds}s · ${DATA.new_this_run} coins nuevas en esta pasada · GMGN no se consulta (bloquea con Cloudflare); los links a GMGN sí funcionan.</p>`;
  }

  // ---------- "No es TikTok" + pestaña Aprendido
  function getPin(force) {
    let pin = localStorage.getItem(LS_PIN);
    if (!pin || force) {
      pin = (prompt("PIN del radar (solo se pide una vez en este dispositivo):") || "").trim();
      if (pin) localStorage.setItem(LS_PIN, pin);
    }
    return pin || null;
  }
  async function api(body) {
    const pin = getPin();
    if (!pin) return { error: "no_pin" };
    try {
      const r = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.assign({ pin }, body)) });
      if (r.status === 404) return { error: "not_configured" };
      const j = await r.json().catch(() => ({}));
      if (j.error === "bad_pin") localStorage.removeItem(LS_PIN);
      return r.ok ? j : { error: j.error || "HTTP " + r.status };
    } catch (e) { return { error: "network" }; }
  }
  const apiErrorText = (e) => ({
    not_configured: "Falta configurar el token en Vercel", bad_pin: "PIN incorrecto (se te volverá a pedir)", no_pin: "Sin PIN no se puede guardar",
    github_auth: "El token de GitHub no es válido o no tiene permiso (Contents: Read and write)", no_branch: "Falta la rama 'feedback' en GitHub",
    network: "Sin conexión con el servidor", conflict: "Conflicto al guardar, prueba otra vez",
  }[e] || "Error al guardar (" + e + ")");

  async function markNotTikTok(cas) {
    const items = cas.map((ca) => { const c = BYCA.get(ca) || { ca }; return { ca, name: c.name, symbol: c.symbol, keys: qkeys(c), dev: c.dev || null }; });
    const now = Date.now();  // mismo valor para la coin y sus clones = un grupo
    for (const ca of cas) NTT[ca] = now;
    saveNtt(); renderCoins();
    const r = await api({ action: "mark", items });
    if (r.ok) {
      cas.forEach((ca) => SERVER.cas.add(ca)); renderLearned();
      toast("Marcada 'No es TikTok' ✓ (todos tus dispositivos)", { label: "Deshacer", fn: () => unmarkNotTikTok(cas) });
    } else if (r.error === "bad_pin" || r.error === "no_pin") {
      for (const ca of cas) delete NTT[ca];
      saveNtt(); renderCoins(); toast(apiErrorText(r.error), null, 3500);
    } else {
      renderLearned();
      toast(apiErrorText(r.error) + " · oculta solo en este dispositivo", { label: "Deshacer", fn: () => unmarkNotTikTok(cas) }, 6000);
    }
  }
  async function unmarkNotTikTok(cas) {
    for (const ca of cas) { delete NTT[ca]; SERVER.cas.delete(ca); }
    saveNtt(); renderCoins(); renderLearned();
    const r = await api({ action: "unmark", cas });
    toast(r.ok ? "Vale, sí es TikTok ✓ Vuelve en la próxima pasada (≤10 min)" : apiErrorText(r.error), null, 3000);
  }
  async function toggleRule(key, off) {
    const r = await api({ action: off ? "rule_off" : "rule_on", key });
    if (r.ok) { SERVER.rules_off = off ? [...new Set(SERVER.rules_off.concat(key))] : SERVER.rules_off.filter((k) => k !== key); renderLearned(); }
    toast(r.ok ? (off ? "Regla desactivada ✓" : "Regla activada ✓") + " Se aplica en la próxima pasada (≤10 min)" : apiErrorText(r.error), null, 3000);
  }

  function renderLearned() {
    const L = DATA.learned || { rules: [], marked: [], blocked_now: [], min_marks: 3 };
    const labels = Object.fromEntries((L.rules || []).map((r) => [r.key, r.label]));
    const keyTxt = (ks) => (ks || []).map((k) => labels[k] || k).join(" + ") || "–";
    let st = "";
    if (SERVER.configured === false) st = `<p class="warnbox">⚠️ Falta configurar el token en Vercel (variables <code>RADAR_GH_TOKEN</code> y <code>RADAR_PIN</code>). Mientras, "No es TikTok" solo oculta en este dispositivo.</p>`;
    else if (SERVER.configured === "noapi") st = `<p class="warnbox">La función <code>/api/not-tiktok</code> no responde aquí (¿estás en local?). "No es TikTok" solo oculta en este dispositivo.</p>`;
    const offSet = new Set((SERVER.configured === true ? SERVER.rules_off : null) || (L.rules || []).filter((r) => r.off).map((r) => r.key));
    const rules = (L.rules || []).map((r) => {
      const off = offSet.has(r.key);
      const state = r.protected ? `<span class="note">🛡️ protegida</span>` : off ? `<span class="note">⏸️ desactivada</span>` : r.active ? `<span class="down">⛔ activa · quita ${r.blocked_now} ahora</span>` : `<span class="note">aprendiendo ${r.count}/${L.min_marks}</span>`;
      const btn = r.protected ? "" : `<button class="link" data-rule="${esc(r.key)}" data-off="${off ? 0 : 1}">${off ? "Activar" : "Desactivar"}</button>`;
      return `<tr><td>${esc(r.label)}</td><td class="num">${r.count}${r.seen > r.count ? ` <span class="note">(+${r.seen - r.count} mixtas)</span>` : ""}</td><td>${state}</td><td>${btn}</td></tr>`;
    }).join("");
    const pubSet = new Set((L.marked || []).map((m) => m.ca));
    // agrupar por pulsación (coin + clones): pendientes por timestamp local, publicadas por "g"
    const groupBy = (arr, keyFn) => { const m = new Map(); for (const x of arr) { const k = keyFn(x); if (!m.has(k)) m.set(k, []); m.get(k).push(x); } return [...m.values()]; };
    const pending = [...new Set([...Object.keys(NTT), ...SERVER.cas])].filter((ca) => !pubSet.has(ca));
    const pendGroups = groupBy(pending, (ca) => NTT[ca] ? "t" + NTT[ca] : ca);
    const pendHtml = pending.length ? `<h3>Pendientes de procesar (${pendGroups.length})</h3><p class="note">Ya están ocultas; el programa las tendrá en cuenta en la próxima pasada (≤10 min).${SERVER.configured !== true ? " Las marcadas sin servidor solo se ocultan en este dispositivo." : ""}</p>` +
      pendGroups.map((cas) => { const c = BYCA.get(cas[0]) || {}; return `<div class="lrow"><span>${esc(c.name || shortAddr(cas[0]))} ${c.symbol ? "$" + esc(c.symbol) : ""}${cas.length > 1 ? ` <span class="note">(+${cas.length - 1} clones)</span>` : ""}${NTT[cas[0]] && !SERVER.cas.has(cas[0]) ? ' <span class="note">(solo este dispositivo)</span>' : ""}</span><button class="link" data-unntt="${esc(cas.join(","))}">Sí es TikTok</button></div>`; }).join("") : "";
    const marked = (L.marked || []).filter((m) => SERVER.configured !== true || SERVER.cas.has(m.ca));
    const markedGroups = groupBy(marked, (m) => m.g || m.ca);
    const markedHtml = marked.length ? markedGroups.map((ms) => { const m = ms[0]; return `<div class="lrow"><span><b>${esc(m.name || shortAddr(m.ca))}</b> ${m.symbol ? "$" + esc(m.symbol) : ""}${ms.length > 1 ? ` (+${ms.length - 1} clones)` : ""} <span class="note">· ${esc(keyTxt(m.keys))} · ${m.ts ? esc(fmtTime(m.ts)) : ""}</span></span><button class="link" data-unntt="${esc(ms.map((x) => x.ca).join(","))}">Sí es TikTok</button></div>`; }).join("") : `<p class="note">Todavía no has marcado ninguna coin.</p>`;
    const blocked = (L.blocked_now || []).length ? `<h3>Quitadas ahora por reglas aprendidas (${L.blocked_now.length})</h3>` + L.blocked_now.map((b) => `<div class="lrow"><span>${esc(b.name || shortAddr(b.ca))} ${b.symbol ? "$" + esc(b.symbol) : ""} <span class="note">· ${esc(keyTxt(b.keys))}</span></span><a href="https://dexscreener.com/solana/${esc(b.ca)}" target="_blank" rel="noopener">ver</a></div>`).join("") : "";
    const devs = (DATA.dev_hot || []).length ? DATA.dev_hot.map((d) => `<div class="lrow"><span><b>${esc(shortAddr(d.dev))}</b> · ${d.count} coins TikTok <span class="note">· ${esc(d.coins.map((x) => x.name || x.symbol).join(", "))}</span></span><span><a href="https://gmgn.ai/sol/address/${esc(d.dev)}" target="_blank" rel="noopener">GMGN</a> · <a href="https://pump.fun/profile/${esc(d.dev)}" target="_blank" rel="noopener">pump.fun</a></span></div>`).join("") : `<p class="note">Ningún dev llega todavía a ${esc(DATA.dev_hot_min || 3)} coins TikTok.</p>`;
    $("#learned").innerHTML = st +
      `<p class="note">Cada vez que pulsas <b>🚫 No es TikTok</b> la coin (y sus clones) desaparece para siempre en todos tus dispositivos. Además el filtro cuenta <b>por qué entró</b>: si un motivo junta <b>${esc(L.min_marks)}</b> marcas de coins que entraron <b>solo</b> por ese motivo, deja de aceptar coins que entren únicamente por él. Las reglas protegidas (p. ej. "tiktok" en el nombre) nunca se bloquean solas.</p>
      <h3>Reglas aprendidas</h3>${rules ? `<table><thead><tr><th>Motivo de entrada</th><th class="num">Marcas</th><th>Estado</th><th></th></tr></thead><tbody>${rules}</tbody></table>` : `<p class="note">Aún no hay marcas.</p>`}
      ${blocked}${pendHtml}<h3>Marcadas "No es TikTok" (${markedGroups.length})</h3>${markedHtml}
      <h3>TikTok devs 🔥 (≥ ${esc(DATA.dev_hot_min || 3)} coins TikTok en ${esc(DATA.dev_hot_window_days || 7)} días)</h3>${devs}
      <p class="note" style="margin-top:16px"><button class="link" id="forgetPin">Cambiar el PIN de este dispositivo</button></p>`;
    const fp = $("#forgetPin"); if (fp) fp.onclick = () => { localStorage.removeItem(LS_PIN); toast("PIN olvidado: se pedirá la próxima vez"); };
  }

  function render() { renderMeta(); renderCoins(); renderTrends(); renderSources(); renderLearned(); }

  // ---------- eventos
  document.addEventListener("click", async (e) => {
    const cp = e.target.closest("[data-copy]");
    if (cp) {
      e.preventDefault();
      const ca = cp.getAttribute("data-copy");
      try { await navigator.clipboard.writeText(ca); } catch (_) {
        const ta = Object.assign(document.createElement("textarea"), { value: ca }); document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
      }
      toast("CA copiado ✓"); return;
    }
    const nb = e.target.closest("[data-ntt]");
    if (nb) { markNotTikTok(nb.dataset.ntt.split(",").filter(Boolean)); return; }
    const ub = e.target.closest("[data-unntt]");
    if (ub) { unmarkNotTikTok(ub.dataset.unntt.split(",").filter(Boolean)); return; }
    const rb = e.target.closest("[data-rule]");
    if (rb) { toggleRule(rb.dataset.rule, rb.dataset.off === "1"); return; }
    const dh = e.target.closest(".b.devhot");
    if (dh) { const el = document.getElementById("dv-" + dh.dataset.devca); if (el) el.hidden = !el.hidden; return; }
    const hb = e.target.closest("[data-hide]");
    if (hb) {
      const cas = hb.dataset.hide.split(",").filter(Boolean);
      const unhide = !!HIDDEN[cas[0]];
      for (const ca of cas) { if (unhide) delete HIDDEN[ca]; else HIDDEN[ca] = Date.now(); }
      saveHidden(); renderCoins(); toast(unhide ? "Vuelve a mostrarse ✓" : "Oculta ✓ (míralas en 'Ver ocultas')"); return;
    }
    const cl = e.target.closest(".b.clones");
    if (cl) { const el = document.getElementById("cl-" + cl.dataset.ca); if (el) el.hidden = !el.hidden; return; }
    const tr = e.target.closest("[data-trend]");
    if (tr) { trendFilter = tr.dataset.trend; $("#hideInactive").checked = false; switchTab("coins"); renderCoins(); return; }
    const tb = e.target.closest(".tabs button");
    if (tb) switchTab(tb.dataset.tab);
  });
  function switchTab(t) {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
    ["coins", "search", "trends", "learned", "sources"].forEach((x) => ($("#tab-" + x).hidden = x !== t));
  }
  ["#q", "#sort", "#mcmin", "#mcmax", "#agemax", "#liqmin", "#hideInactive", "#groupClones", "#onlyNew", "#onlyPaid", "#onlyDevHot", "#showHidden"].forEach((s) =>
    $(s).addEventListener("input", () => { saveFilters(); DATA && renderCoins(); }));
  $("#resetF").onclick = () => { ["#mcmin", "#mcmax", "#agemax", "#liqmin"].forEach((s) => ($(s).value = "")); saveFilters(); renderCoins(); };
  $("#reload").onclick = () => { load(); toast("Recargando…"); };

  restoreFilters();
  load();
  setInterval(load, CFG.refreshSeconds * 1000);
  setInterval(() => DATA && renderMeta(), 30000);
})();

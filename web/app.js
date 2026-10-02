(() => {
  "use strict";
  const CFG = Object.assign({ dataUrl: "data.json", refreshSeconds: 120 }, window.RADAR_CONFIG || {});
  const $ = (s) => document.querySelector(s);
  const LS_VISIT = "ttr_last_visit", LS_FILTERS = "ttr_filters";
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
  const toast = (t) => { const el = $("#toast"); el.textContent = t; el.classList.add("show"); setTimeout(() => el.classList.remove("show"), 1400); };
  const normName = (s) => String(s || "").toLowerCase().replace(/[^0-9a-z\u00c0-\uffff]/g, "");

  const reasonCls = (t) => ["kw_name", "kw_desc", "link_tiktok", "meta_tiktok"].includes(t) ? "direct" : t === "trend" || t === "manual" ? "trend" : t === "meta_brainrot" ? "brainrot" : "other";
  const reasonShort = (r) => ({ kw_name: "TikTok en nombre", kw_desc: "TikTok en descripción", link_tiktok: "Link TikTok", meta_tiktok: "Cat. TikTok (DexS)", meta_brainrot: "Brainrot (DexS)", phrase: "Frase viral" }[r.t] || (r.t === "trend" ? (r.d.match(/#\S+/) || ["Trend"])[0] : r.t === "manual" ? "Trend manual" : r.t));

  // ---------- carga de datos
  async function fetchJson(url) {
    const sep = url.includes("?") ? "&" : "?";
    const r = await fetch(url + sep + "t=" + Math.floor(Date.now() / 60000), { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  async function load() {
    const urls = [CFG.dataUrl].concat(CFG.fallbackUrl && CFG.fallbackUrl !== CFG.dataUrl ? [CFG.fallbackUrl] : []);
    let err = null;
    for (const u of urls) {
      try {
        const d = await fetchJson(u);
        // si la copia de respaldo es más vieja que lo que ya tenemos, no la usamos
        if (DATA && d.generated_ms < DATA.generated_ms) return;
        DATA = d; render(); return;
      } catch (e) { err = e; }
    }
    $("#meta").innerHTML = `<span class="stale">No se pudo cargar data.json (${esc(err && err.message)}).</span>`;
  }

  function renderMeta() {
    const g = DATA.generated_ms || Date.parse(DATA.generated_at);
    const mins = (Date.now() - g) / 60000;
    const newSince = prevVisit ? DATA.coins.filter((c) => c.first_seen > prevVisit).length : 0;
    $("#meta").innerHTML = `Última actualización: <b>${fmtTime(g)}</b> <span class="${mins > 30 ? "stale" : ""}">(hace ${ago(g)})</span> · ${DATA.total} coins (${DATA.active} vivas)` +
      (prevVisit ? ` · <b>${newSince}</b> nuevas desde tu última visita` : "");
  }

  // ---------- filtros
  const F = () => ({
    q: $("#q").value.trim().toLowerCase(), sort: $("#sort").value,
    mcmin: parseFloat($("#mcmin").value) || 0, mcmax: parseFloat($("#mcmax").value) || Infinity,
    agemax: parseFloat($("#agemax").value) || Infinity, liqmin: parseFloat($("#liqmin").value) || 0,
    hideInactive: $("#hideInactive").checked, group: $("#groupClones").checked, onlyNew: $("#onlyNew").checked,
    reasons: [...document.querySelectorAll("#reasonChips input:checked")].map((i) => i.value),
  });
  const saveFilters = () => { const f = F(); delete f.q; localStorage.setItem(LS_FILTERS, JSON.stringify(f)); };
  function restoreFilters() {
    try {
      const f = JSON.parse(localStorage.getItem(LS_FILTERS) || "null"); if (!f) return;
      $("#sort").value = f.sort || "new";
      if (f.mcmin) $("#mcmin").value = f.mcmin; if (f.mcmax !== null && isFinite(f.mcmax)) $("#mcmax").value = f.mcmax;
      if (f.agemax !== null && isFinite(f.agemax)) $("#agemax").value = f.agemax; if (f.liqmin) $("#liqmin").value = f.liqmin;
      $("#hideInactive").checked = f.hideInactive !== false; $("#groupClones").checked = f.group !== false; $("#onlyNew").checked = !!f.onlyNew;
      document.querySelectorAll("#reasonChips input").forEach((i) => (i.checked = !f.reasons || f.reasons.includes(i.value)));
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
      if (f.hideInactive && (c.inactive || c.dead)) return false;
      if (f.onlyNew && !(prevVisit && c.first_seen > prevVisit)) return false;
      if (mc < f.mcmin || mc > f.mcmax) return false;
      if ((m.liq || 0) < f.liqmin) return false;
      const created = c.created || c.first_seen;
      if ((now - created) / 3.6e6 > f.agemax) return false;
      if (!c.reasons.some((r) => f.reasons.includes(reasonCls(r.t)))) return false;
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
    const key = sortKey[f.sort] || sortKey.new;
    groups.sort((a, b) => {
      const ka = f.sort === "new" ? Math.max(key(a.main), ...a.clones.map(key)) : key(a.main);
      const kb = f.sort === "new" ? Math.max(key(b.main), ...b.clones.map(key)) : key(b.main);
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
    for (const r of c.reasons) badges.push(`<span class="b ${reasonCls(r.t)}" title="${esc(r.d)}">${esc(reasonShort(r))}</span>`);
    if ((c.flags || []).includes("mc_sospechoso")) badges.push(`<span class="b warn" title="MC muy alto con liquidez casi nula">MC dudoso</span>`);
    if ((c.flags || []).includes("bonding_curve")) badges.push(`<span class="b" title="Aún en la bonding curve de pump.fun">Bonding curve</span>`);
    if (c.pump?.complete) badges.push(`<span class="b" title="Graduada de pump.fun">Graduada</span>`);
    if (g.clones.length) badges.push(`<span class="b clones" data-ca="${esc(c.ca)}">+${g.clones.length} clones</span>`);
    const link = (u, t, cls = "") => safeUrl(u) ? `<a class="${cls}" href="${safeUrl(u)}" target="_blank" rel="noopener noreferrer">${t}</a>` : "";
    const tts = (L.tiktok || []).map((u, i) => link(u, "TikTok" + (L.tiktok.length > 1 ? " " + (i + 1) : ""), "tt")).join("");
    const clones = g.clones.length ? `<div class="clist" id="cl-${esc(c.ca)}" hidden>${g.clones.map((x) =>
      `<div><span>${esc(x.symbol)} · ${money(x.metrics?.mc)} · hace ${ago(x.created || x.first_seen)}</span><span><a href="${safeUrl(x.links?.gmgn) || "#"}" target="_blank" rel="noopener">GMGN</a> · <a href="#" data-copy="${esc(x.ca)}">copiar CA</a></span></div>`).join("")}</div>` : "";
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
      <div class="links">${link(L.gmgn, "GMGN")}${link(L.dexscreener, "DexScreener")}${link(L.pumpfun, "pump.fun")}${link(L.x, "X")}${tts}${link(L.website, "Web")}${link(L.telegram, "TG")}</div>
      ${clones}
    </article>`;
  }

  function renderCoins() {
    const groups = filtered();
    const n = groups.reduce((a, g) => a + 1 + g.clones.length, 0);
    $("#summary").innerHTML = `${groups.length} resultados (${n} coins)` + (trendFilter ? ` · filtrando trend <b>#${esc(trendFilter)}</b> <button class="link" id="clearTrend">quitar</button>` : "");
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
    const names = { tiktok_creative_center: "TikTok Creative Center (hashtags)", dexscreener_meta_tiktok: "DexScreener · categoría TikTok", dexscreener_meta_brainrot: "DexScreener · categoría Brainrot", dexscreener_search: "DexScreener · búsqueda", dexscreener_profiles: "DexScreener · perfiles nuevos", dexscreener_boosts: "DexScreener · boosts", dexscreener_boosts_top: "DexScreener · top boosts", dexscreener_cto: "DexScreener · CTOs", dexscreener_tokens: "DexScreener · métricas", pumpfun_new: "pump.fun · coins nuevas", pumpfun_active: "pump.fun · coins activas" };
    const s = DATA.sources || {};
    $("#sources").innerHTML = `<table><thead><tr><th>Fuente</th><th>Estado</th><th class="num">Items</th><th class="hide-m">Error</th></tr></thead><tbody>${Object.entries(s).map(([k, v]) =>
      `<tr><td>${esc(names[k] || k)}</td><td>${v.ok && !v.fail ? "🟢 OK" : v.ok ? "🟡 parcial" : "🔴 falla"}</td><td class="num">${v.items}</td><td class="hide-m note">${esc(v.last_error || "")}</td></tr>`).join("")}</tbody></table>
      <p class="note">Pasada: ${DATA.run_seconds}s · ${DATA.new_this_run} coins nuevas en esta pasada · GMGN no se consulta (bloquea con Cloudflare); los links a GMGN sí funcionan.</p>`;
  }

  function render() { renderMeta(); renderCoins(); renderTrends(); renderSources(); }

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
    const cl = e.target.closest(".b.clones");
    if (cl) { const el = document.getElementById("cl-" + cl.dataset.ca); if (el) el.hidden = !el.hidden; return; }
    const tr = e.target.closest("[data-trend]");
    if (tr) { trendFilter = tr.dataset.trend; $("#hideInactive").checked = false; switchTab("coins"); renderCoins(); return; }
    const tb = e.target.closest(".tabs button");
    if (tb) switchTab(tb.dataset.tab);
  });
  function switchTab(t) {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
    ["coins", "trends", "sources"].forEach((x) => ($("#tab-" + x).hidden = x !== t));
  }
  ["#q", "#sort", "#mcmin", "#mcmax", "#agemax", "#liqmin", "#hideInactive", "#groupClones", "#onlyNew"].forEach((s) =>
    $(s).addEventListener("input", () => { saveFilters(); DATA && renderCoins(); }));
  $("#reasonChips").addEventListener("change", () => { saveFilters(); DATA && renderCoins(); });
  $("#resetF").onclick = () => { ["#mcmin", "#mcmax", "#agemax", "#liqmin"].forEach((s) => ($(s).value = "")); document.querySelectorAll("#reasonChips input").forEach((i) => (i.checked = true)); saveFilters(); renderCoins(); };
  $("#reload").onclick = () => { load(); toast("Recargando…"); };

  restoreFilters();
  load();
  setInterval(load, CFG.refreshSeconds * 1000);
  setInterval(() => DATA && renderMeta(), 30000);
})();

// Pestaña "Buscador": busca una palabra en TikTok a través del box (via /api/search en Vercel). Sin IA.
(() => {
  "use strict";
  const CFG = window.RADAR_CONFIG || {};
  const API = CFG.searchApi || "/api/search";
  const LS_PIN = "ttr_pin", LS_RECENT = "ttr_srecent";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https:\/\//i.test(u || "") ? esc(u) : null);
  const num = (v) => v == null || isNaN(v) ? "–" : v >= 1e9 ? (v / 1e9).toFixed(1) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(Math.round(v));
  const agoS = (t) => { if (!t) return "–"; const d = (Date.now() / 1000 - t) / 86400; return d < 1 / 24 ? Math.max(1, Math.round(d * 1440)) + " min" : d < 1 ? Math.round(d * 24) + " h" : d < 60 ? Math.round(d) + " d" : d < 730 ? Math.round(d / 30) + " meses" : (d / 365).toFixed(1) + " años"; };
  const fmtDay = (t) => new Date(t * 1000).toLocaleDateString("es-ES", { day: "2-digit", month: "short" });
  let busy = false, last = null, inited = false, status = null;

  function getPin(force) {
    let pin = localStorage.getItem(LS_PIN);
    if (!pin || force) {
      pin = (prompt("PIN del radar (el mismo de \"No es TikTok\"; solo se pide una vez en este dispositivo):") || "").trim();
      if (pin) localStorage.setItem(LS_PIN, pin);
    }
    return pin || null;
  }
  const ERR = {
    no_pin: "Sin PIN no se puede buscar. Pulsa Buscar otra vez y mételo.",
    bad_pin: "PIN incorrecto: se te volverá a pedir.",
    not_configured: "Falta configurar el PIN en Vercel (variable RADAR_PIN). Mira el README → Buscador.",
    box_offline: "El box está apagado o reiniciándose (el servicio del Buscador no responde). Prueba en unos minutos.",
    box_timeout: "El box ha tardado demasiado. Prueba otra vez.",
    busy: "El box está ocupado con otras búsquedas: prueba en unos segundos.",
    locked: "Demasiados PIN incorrectos: el box bloquea las búsquedas durante un rato.",
    check_failed: "El box no ha podido comprobar el PIN con la web. Prueba otra vez.",
    noapi: "La función /api/search no existe aquí (¿estás en local con http.server?). Usa la web publicada.",
    network: "Sin conexión con el servidor.",
    watch_full: "Ya sigues el máximo de palabras (25).",
    bad_query: "Escribe una palabra válida.",
  };
  const errText = (e) => ERR[e] || "Error (" + e + ")";

  async function api(body, needPin = true) {
    const pin = needPin ? getPin() : null;
    if (needPin && !pin) return { error: "no_pin" };
    try {
      const r = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.assign({ pin }, body)) });
      if (r.status === 404) return { error: "noapi" };
      const j = await r.json().catch(() => ({ error: "HTTP " + r.status }));
      if (j.error === "bad_pin") localStorage.removeItem(LS_PIN);
      return r.ok ? j : { error: j.error || "HTTP " + r.status };
    } catch (_) { return { error: "network" }; }
  }

  async function checkStatus() {
    const el = $("#sstatus");
    try {
      const r = await fetch(API, { cache: "no-store" });
      if (r.status === 404) { status = "noapi"; }
      else { const j = await r.json(); status = !j.configured ? "not_configured" : j.box === "online" ? "online" : "box_offline"; }
    } catch (_) { status = "noapi"; }
    el.innerHTML = status === "online" ? `<span class="up">🟢 Box conectado</span> · cada búsqueda nueva tarda ~5-10 s (se guarda 45 min)`
      : `<span class="warnbox inline">⚠️ ${esc(errText(status))}</span>`;
    return status;
  }

  // ---------- gráfica SVG mínima (sin librerías)
  function chart(series, opts = {}) {
    const W = Math.round(Math.max(280, Math.min(1100, (($("#sresult") || {}).clientWidth || 640) - 26))), H = 190, P = { l: 38, r: 10, t: 12, b: 24 };
    const all = series.flatMap((s) => s.points);
    if (all.length < 2) return "";
    const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), yMax = opts.yMax || Math.max(1, ...ys), yMin = opts.yMin != null ? opts.yMin : 0;
    const X = (x) => P.l + ((x - x0) / Math.max(1, x1 - x0)) * (W - P.l - P.r);
    const Y = (y) => H - P.b - ((y - yMin) / Math.max(1e-9, yMax - yMin)) * (H - P.t - P.b);
    const grid = [0, 0.5, 1].map((f) => { const v = yMin + f * (yMax - yMin), y = Y(v); return `<line x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" class="g"/><text x="${P.l - 4}" y="${y + 4}" text-anchor="end">${esc(opts.fmt ? opts.fmt(v) : Math.round(v))}</text>`; }).join("");
    const xl = [x0, (x0 + x1) / 2, x1].map((x, i) => `<text x="${X(x)}" y="${H - 6}" text-anchor="${["start", "middle", "end"][i]}">${esc(fmtDay(x))}</text>`).join("");
    const lines = series.filter((s) => s.points.length > 1).map((s) => `<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${s.points.map((p) => X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join(" ")}"/>` +
      (s.points.length < 15 ? s.points.map((p) => `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="3" fill="${s.color}"/>`).join("") : "")).join("");
    const legend = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img">${grid}${xl}${lines}</svg><div class="legend">${legend}</div></div>`;
  }

  const dirTxt = (d) => ({ "sube fuerte": "▲▲ sube fuerte", sube: "▲ sube", baja: "▼ baja", estable: "■ estable", "poco volumen": "· poco volumen", "sin datos": "· sin datos" }[d] || d || "–");
  const dirCls = (d) => (d || "").startsWith("sube") ? "up" : d === "baja" ? "down" : "note";

  function videoRow(v) {
    const cover = safeUrl(v.cover) ? `<img loading="lazy" referrerpolicy="no-referrer" src="${safeUrl(v.cover)}" alt="" onerror="this.remove()">` : "";
    return `<a class="vrow" href="${safeUrl(v.url) || "#"}" target="_blank" rel="noopener noreferrer">${cover}<div class="vtxt"><div class="vdesc">${esc(v.desc || "(sin texto)")}</div>
      <div class="vmeta">@${esc(v.author)} · hace ${esc(agoS(v.t))} · 👁 ${num(v.views)} · ❤ ${num(v.likes)} · 💬 ${num(v.comments)}${v.src === "busqueda" ? ' · <span class="note">búsqueda</span>' : ""}</div></div></a>`;
  }

  function render(r) {
    last = r;
    const h = r.hashtag || {}, s = r.sample || {}, v = r.verdict || {}, b = s.buckets || {}, n = s.n || 0;
    const tagLink = h.found ? `<a href="${safeUrl(h.url)}" target="_blank" rel="noopener">#${esc(h.tag)}</a>` : `<span>“${esc(r.q)}”</span>`;
    const watching = !!r.watching;
    const head = `<div class="scard shead"><div class="sh1"><div class="sq">${tagLink}</div><span class="verdict v-${esc(v.verdict)}">${esc(v.label || "–")}</span></div>
      <div class="note">${r.cached ? `Datos de hace ${Math.max(1, Math.round((r.cache_age_s || 0) / 60))} min (guardados)` : `Recién buscado (${esc(r.took_s)} s)`} · puntuación ${esc(v.score)}
        <button class="link" id="sfresh">↻ Actualizar</button>
        <button class="watchbtn${watching ? " on" : ""}" id="swatchbtn">${watching ? "⭐ Siguiendo" : "☆ Seguir (foto diaria)"}</button></div>
      ${(v.why || []).length ? `<details class="why"><summary>¿Por qué este veredicto?</summary><ul>${v.why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}</div>`;
    const notFound = h && h.found === false ? `<p class="warnbox">No existe el hashtag <b>#${esc(r.tag)}</b> en TikTok. Abajo solo salen vídeos de la búsqueda normal que mencionan la palabra.</p>` : "";
    const stat = (k, val, t) => `<div class="stat"${t ? ` title="${esc(t)}"` : ""}><div class="k">${k}</div><div class="v">${val}</div></div>`;
    const stats = `<div class="scard"><h4>Números</h4><div class="stats">
      ${stat("Vídeos #" + esc(h.tag || r.tag), h.found ? num(h.videos) : "–", "Total de vídeos con el hashtag (dato de TikTok)")}
      ${stat("Views totales", h.found ? num(h.views) : "–", "Suma de views de todos los vídeos del hashtag")}
      ${stat("Views / vídeo", h.found && h.videos ? num(h.views / h.videos) : "–")}
      ${stat("Muestra", n + " vídeos", `${s.from_hashtag || 0} del hashtag (los más populares) + ${s.from_search || 0} de la búsqueda`)}
      ${stat("Mediana views", num(s.views_median))}${stat("Máx views", num(s.views_max))}
      ${stat("Mediana likes", num(s.likes_median))}${stat("Máx likes", num(s.likes_max))}
      ${stat("Últimas 24 h", s.last24h ?? "–", "Vídeos de la muestra publicados en las últimas 24 h")}${stat("Últimos 7 días", s.last7d ?? "–")}
      ${stat("Últimos 30 días", s.last30d ?? "–")}${stat("Edad mediana", s.median_age_days != null ? agoS(Date.now() / 1000 - s.median_age_days * 86400) : "–")}
    </div></div>`;
    const bl = [["h24", "< 24 h"], ["d7", "1-7 días"], ["d30", "7-30 días"], ["y1", "1-12 meses"], ["old", "> 1 año"]];
    const maxB = Math.max(1, ...bl.map(([k]) => b[k] || 0));
    const bars = n ? `<div class="scard"><h4>Recientes vs antiguos <span class="note">(muestra de ${n} vídeos)</span></h4>${bl.map(([k, l], i) =>
      `<div class="bar"><span class="bl">${l}</span><span class="bt"><span class="bf b${i}" style="width:${((b[k] || 0) / maxB) * 100}%"></span></span><span class="bn">${b[k] || 0} <span class="note">(${Math.round(((b[k] || 0) / n) * 100)}%)</span></span></div>`).join("")}
      <p class="note">TikTok da los vídeos del hashtag ordenados por popularidad, no por fecha: en hashtags enormes y antiguos lo reciente sale poco aunque siga activo.</p></div>` : "";
    const tw = (r.trends || {}).web || {}, ty = (r.trends || {}).youtube || {};
    const tchart = chart([{ name: "Google (web)", color: "#25f4ee", points: tw.points || [] }, { name: "YouTube", color: "#fe2c55", points: ty.points || [] }], { yMax: 100 });
    const trends = `<div class="scard"><h4>Interés últimos 90 días <span class="note">(Google Trends, 0-100)</span></h4>
      <div class="tdirs">Web: <b class="${dirCls(tw.direction)}">${esc(dirTxt(tw.direction))}</b>${tw.ratio ? ` <span class="note">×${esc(tw.ratio)}</span>` : ""} · YouTube: <b class="${dirCls(ty.direction)}">${esc(dirTxt(ty.direction))}</b>${ty.ratio ? ` <span class="note">×${esc(ty.ratio)}</span>` : ""}</div>
      ${tchart || `<p class="note">Google Trends no ha dado datos para esta palabra (o ha limitado las peticiones).</p>`}
      <p class="note">TikTok no da su gráfica sin login: esto es lo que se busca la palabra en Google y YouTube. ×N = media de los últimos 7 días frente a las 4 semanas anteriores.</p></div>`;
    const g = r.growth || {};
    const snaps = (g.snaps || []).filter((x) => x.videos);
    const gchart = snaps.length >= 2 ? chart([{ name: "Vídeos con #" + (h.tag || r.tag), color: "#14f195", points: snaps.map((x) => [x.t, x.videos]) }], { yMin: Math.min(...snaps.map((x) => x.videos)) * 0.98, yMax: Math.max(...snaps.map((x) => x.videos)) * 1.02, fmt: num }) : "";
    const growth = watching ? `<div class="scard"><h4>⭐ Crecimiento real (watchlist)</h4>${gchart || `<p class="note">Se guarda una foto al día (nº de vídeos y views del hashtag). Con 2 fotos ya sale la curva. Fotos: ${(g.snaps || []).length}.</p>`}
      ${g.videos_pct_day != null ? `<p>Vídeos: <b class="${g.videos_pct_day > 0 ? "up" : "note"}">${g.videos_pct_day > 0 ? "+" : ""}${esc(g.videos_pct_day)}%/día</b> · Views: <b>${g.views_pct_day != null ? (g.views_pct_day > 0 ? "+" : "") + esc(g.views_pct_day) + "%/día" : "–"}</b></p>` : ""}</div>` : "";
    const tr = s.top_recent || [];
    const vids = `<div class="scard"><h4>🔥 Lo más visto de los últimos 7 días</h4>${tr.length ? tr.map(videoRow).join("") : `<p class="note">Ningún vídeo de los últimos 7 días en la muestra.</p>`}
      <h4>Top 10 por views <span class="note">(muestra)</span></h4>${(s.top || []).map(videoRow).join("") || `<p class="note">Sin vídeos.</p>`}</div>`;
    const rel = (r.related || []).map((x) => `<button class="chip" data-sq="${esc(x.tag)}" title="Sale en ${esc(x.count)} vídeos de la muestra">#${esc(x.tag)} <span class="note">${esc(x.count)}</span></button>`).join("");
    const relT = (r.related_tikwm || []).map((x) => `<button class="chip" data-sq="${esc(x.tag)}" title="${esc(num(x.videos))} vídeos · ${esc(num(x.views))} views">#${esc(x.tag)} <span class="note">${num(x.videos)}</span></button>`).join("");
    const related = rel || relT ? `<div class="scard"><h4>Hashtags relacionados</h4>${rel ? `<p class="note">Los que más se repiten en los vídeos de la muestra (nº de vídeos):</p><div class="chips">${rel}</div>` : ""}
      ${relT ? `<p class="note">Hashtags parecidos (tikwm, nº de vídeos totales):</p><div class="chips">${relT}</div>` : ""}</div>` : "";
    const errs = (r.errors || []).length ? `<p class="note warnline">Fuentes con fallo (resultado parcial): ${esc(r.errors.join(" · "))}</p>` : "";
    $("#sresult").innerHTML = head + notFound + errs + `<div class="sgrid">${stats}${bars}</div>` + trends + growth + vids + related;
    $("#sfresh").onclick = () => doSearch(r.q, true);
    $("#swatchbtn").onclick = () => toggleWatch(r.q, !watching);
  }

  function recent(q) {
    let arr = []; try { arr = JSON.parse(localStorage.getItem(LS_RECENT) || "[]"); } catch (_) {}
    if (q) { arr = [q, ...arr.filter((x) => x.toLowerCase() !== q.toLowerCase())].slice(0, 8); localStorage.setItem(LS_RECENT, JSON.stringify(arr)); }
    $("#srecent").innerHTML = arr.length ? `<span class="note">Recientes:</span> ` + arr.map((x) => `<button class="chip" data-sq="${esc(x)}">${esc(x)}</button>`).join("") : "";
  }

  async function doSearch(q, fresh) {
    q = String(q || "").replace(/\s+/g, " ").trim().slice(0, 60);
    if (!q || busy) return;
    $("#sq").value = q;
    busy = true; $("#sbtn").disabled = true;
    $("#sresult").innerHTML = `<div class="scard loading"><span class="spin"></span> Buscando “${esc(q)}” en TikTok, Google Trends y tikwm… (~5-10 s)</div>`;
    const r = await api({ action: "search", q, fresh: !!fresh });
    busy = false; $("#sbtn").disabled = false;
    if (r.error) {
      const st = ["box_offline", "not_configured", "noapi"].includes(r.error);
      $("#sresult").innerHTML = st ? "" : `<p class="warnbox">⚠️ ${esc(errText(r.error))}</p>`;
      if (st) { $("#sstatus").innerHTML = `<span class="warnbox inline">⚠️ ${esc(errText(r.error))}</span>`; checkStatus(); }
      return;
    }
    recent(q); render(r);
    if (status !== "online") checkStatus();
  }

  async function toggleWatch(q, on) {
    const r = await api({ action: "watch", q, on });
    if (r.error) { alert(errText(r.error)); return; }
    renderWatch(r.watchlist || []);
    if (last && last.q === q) doSearch(q);   // sale de la caché al instante, ya con la foto de la watchlist
  }

  function renderWatch(list) {
    $("#swatch").innerHTML = list.length ? list.map((w) => { const g = w.growth || {}, l = w.last || {};
      return `<div class="lrow"><span><button class="link" data-sq="${esc(w.q)}"><b>${esc(w.q)}</b></button> <span class="note">· ${l.videos ? num(l.videos) + " vídeos" : "sin foto aún"}${g.videos_pct_day != null ? ` · <span class="${g.videos_pct_day > 0 ? "up" : ""}">${g.videos_pct_day > 0 ? "+" : ""}${esc(g.videos_pct_day)}%/día</span>` : ""} · ${esc(w.snaps)} fotos</span></span>
        <button class="link" data-unwatch="${esc(w.q)}">Dejar de seguir</button></div>`; }).join("")
      : `<p class="note">No sigues ninguna palabra. Pulsa “☆ Seguir” en un resultado: el box guarda una foto al día (vídeos y views del hashtag) y verás si crece de verdad.</p>`;
  }
  async function loadWatch() {
    if (!localStorage.getItem(LS_PIN) || status !== "online") return;
    const r = await api({ action: "watchlist" });
    if (!r.error) renderWatch(r.watchlist || []);
  }

  async function init() {
    if (inited) return; inited = true;
    recent(); renderWatch([]);
    await checkStatus(); loadWatch();
    const p = new URLSearchParams(location.search).get("buscar");
    if (p) doSearch(p);
  }

  document.addEventListener("click", (e) => {
    const t = e.target.closest(".tabs button[data-tab=search]");
    if (t) { init(); setTimeout(() => $("#sq").focus(), 50); return; }
    const c = e.target.closest("[data-sq]");
    if (c) { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); doSearch(c.dataset.sq); return; }
    const u = e.target.closest("[data-unwatch]");
    if (u) { toggleWatch(u.dataset.unwatch, false); }
  });
  document.addEventListener("submit", (e) => { if (e.target.id === "sform") { e.preventDefault(); doSearch($("#sq").value); } });
  if (new URLSearchParams(location.search).get("buscar") || location.hash === "#buscador") {
    window.addEventListener("load", () => { const b = document.querySelector(".tabs button[data-tab=search]"); if (b) b.click(); });
  }
})();

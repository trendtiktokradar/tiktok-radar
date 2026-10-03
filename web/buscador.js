// Pestaña "Buscador": busca una palabra en TikTok a través del box (via /api/search en Vercel). Sin IA.
(() => {
  "use strict";
  const CFG = window.RADAR_CONFIG || {};
  const API = CFG.searchApi || "/api/search";
  const LS_PIN = "ttr_pin", LS_RECENT = "ttr_srecent";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https:\/\//i.test(u || "") ? esc(u) : null);
  const num = (v) => (v == null || isNaN(v) ? "–" : v >= 1e9 ? (v / 1e9).toFixed(1) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(Math.round(v))).replace(/\.0(?=[KMB])/, "");
  const agoS = (t) => { if (!t) return "–"; const d = (Date.now() / 1000 - t) / 86400; return d < 1 / 24 ? Math.max(1, Math.round(d * 1440)) + " min" : d < 1 ? Math.round(d * 24) + " h" : d < 60 ? Math.round(d) + " d" : d < 730 ? Math.round(d / 30) + " meses" : (d / 365).toFixed(1) + " años"; };
  const fmtDay = (t) => new Date(t * 1000).toLocaleDateString("es-ES", { day: "2-digit", month: "short" });
  let busy = false, last = null, inited = false, status = null;
  let period = [7, 30, 90].includes(Number(localStorage.getItem("ttr_speriod"))) ? Number(localStorage.getItem("ttr_speriod")) : 30;
  const hiddenSeries = new Set();
  const fmtLong = (t) => new Date(t * 1000).toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric" });
  const daysTxt = (d) => d < 1 ? "menos de 1 día" : d < 2 ? "1 día" : Math.round(d) + " días";
  // ---------- fechas en hora de Madrid (igual que el box)
  const madridDay = (ms) => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Madrid", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
  const addDays = (day, k) => { const d = new Date(day + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + k); return d.toISOString().slice(0, 10); };
  const dayLabel = (day, long) => new Date(day + "T12:00:00Z").toLocaleDateString("es-ES", long ? { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" } : { day: "numeric", month: "short", timeZone: "UTC" });


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
    const trends = `<div class="scard" id="tcard"><h4>Tendencia <span class="note">(Google, YouTube y muestra de TikTok)</span></h4>
      <div class="tdirs">Google 90 d: Web <b class="${dirCls(tw.direction)}">${esc(dirTxt(tw.direction))}</b>${tw.ratio ? ` <span class="note">×${esc(tw.ratio)}</span>` : ""} · YouTube <b class="${dirCls(ty.direction)}">${esc(dirTxt(ty.direction))}</b>${ty.ratio ? ` <span class="note">×${esc(ty.ratio)}</span>` : ""}</div>
      <div class="seg" id="tperiod">${[7, 30, 90].map((d) => `<button data-period="${d}" class="${d === period ? "on" : ""}">${d} días</button>`).join("")}</div>
      <div id="tchart"></div></div>`;
    const fv = r.first_viral, rise = r.rise || {};
    const viral = fv ? `<div class="scard"><h4>🚀 Primer vídeo viral <span class="note">(muestra)</span></h4>
      ${fv.fallback ? `<p class="note">Ningún vídeo de la muestra llega a ${num(fv.threshold)} views. El más visto:</p>`
        : `<p>El vídeo más antiguo con <b>≥ ${num(fv.threshold)} views</b> es del <b>${esc(fmtLong(fv.video.t))}</b> · lleva <b>${esc(daysTxt(fv.days_since))}</b> <span class="note">(${esc(fv.count_over)} vídeos de la muestra pasan el umbral)</span></p>`}
      ${videoRow(fv.video)}
      <p class="rise">${rise.rising ? `📈 El volumen de la muestra empezó a subir el <b>${esc(fmtLong(Date.parse(rise.day + "T12:00:00") / 1000))}</b> (hace ${esc(daysTxt(rise.days_since))}): ${esc(rise.now7)} vídeos en los últimos 7 días frente a ~${esc(Math.round(rise.base7))} normales.`
        : `➖ No se ve una subida reciente del volumen en la muestra${rise.now7 != null ? ` (${esc(rise.now7)} vídeos en 7 días, lo normal ~${esc(Math.round(rise.base7 || 0))})` : rise.reason ? ` (${esc(rise.reason)})` : ""}.`}</p>
      <p class="note">Solo con los ~${esc(n)} vídeos de la muestra: puede haber vídeos virales más antiguos que TikTok no nos enseña.</p></div>` : "";
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
    $("#sresult").innerHTML = head + notFound + errs + `<div class="sgrid">${stats}${bars}</div>` + trends + viral + growth + vids + related;
    drawTrend(r);
    if (period === 7 && !r._t7) setPeriod(7);
    $("#sfresh").onclick = () => doSearch(r.q, true);
    $("#swatchbtn").onclick = () => toggleWatch(r.q, !watching);
  }

  // ---------- gráfica grande: Google web + YouTube + TikTok (muestra), 7/30/90 días, con tooltip táctil
  const SER = [
    { key: "web", name: "Google web", color: "#4c8dff", type: "line" },
    { key: "yt", name: "YouTube", color: "#ff4d4d", type: "line" },
    { key: "tt", name: "TikTok vídeos/día", color: "#25f4ee", type: "line", w: 3 },
    { key: "ttv", name: "TikTok views/día", color: "rgba(254,44,85,.38)", type: "bar" },
  ];
  function seriesFor(r, P) {
    const today = madridDay(Date.now());
    const days = Array.from({ length: P }, (_, i) => addDays(today, i - (P - 1)));
    const src = P === 7 ? r._t7 : r.trends;
    const gmap = (k) => { const m = new Map(((src || {})[k] || {}).days || []); const vals = days.map((d) => (m.has(d) ? m.get(d) : null));
      const mx = Math.max(0, ...vals.filter((v) => v != null));
      // 30 días = recorte de la curva de 90: se reescala para que el pico del periodo sea 100 (como hace Google)
      return P === 30 ? vals.map((v) => (v == null ? null : mx ? Math.round((v * 1000) / mx) / 10 : 0)) : vals; };
    const tmap = new Map((r.tiktok_days || []).map((x) => [x[0], x]));
    const tn = days.map((d) => (tmap.get(d) || [d, 0, 0])[1]), tv = days.map((d) => (tmap.get(d) || [d, 0, 0])[2]);
    const mn = Math.max(0, ...tn), mv = Math.max(0, ...tv);
    return { days, web: gmap("web"), yt: gmap("youtube"), tn, tv, tt: tn.map((v) => (mn ? (v * 100) / mn : 0)), ttv: tv.map((v) => (mv ? (v * 100) / mv : 0)),
      N: tn.reduce((a, b) => a + b, 0), gLoaded: !!src };
  }
  function drawTrend(r) {
    const box = $("#tchart"); if (!box) return;
    const P = period, D = seriesFor(r, P);
    const W = Math.round(Math.max(300, Math.min(1150, box.clientWidth || 640))), H = W < 520 ? 250 : 300, pad = { l: 30, r: 8, t: 10, b: 26 };
    const n = D.days.length, cw = (W - pad.l - pad.r) / n;
    const X = (i) => pad.l + cw * (i + 0.5), Y = (v) => H - pad.b - (v / 100) * (H - pad.t - pad.b);
    const grid = [0, 25, 50, 75, 100].map((v) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(v)}" y2="${Y(v)}" class="g"/><text x="${pad.l - 5}" y="${Y(v) + 4}" text-anchor="end">${v}</text>`).join("");
    const step = Math.max(1, Math.ceil(n / (W < 520 ? 5 : 8)));
    const xl = D.days.map((d, i) => ((n - 1 - i) % step === 0 ? `<text x="${X(i)}" y="${H - 8}" text-anchor="middle">${esc(dayLabel(d))}</text>` : "")).join("");
    let body = "";
    if (!hiddenSeries.has("ttv")) body += D.ttv.map((v, i) => (v > 0 ? `<rect x="${(X(i) - cw * 0.38).toFixed(1)}" width="${(cw * 0.76).toFixed(1)}" y="${Y(v).toFixed(1)}" height="${(Y(0) - Y(v)).toFixed(1)}" fill="${SER[3].color}" rx="2"/>` : "")).join("");
    for (const s of SER.filter((x) => x.type === "line" && !hiddenSeries.has(x.key))) {
      const vals = D[s.key]; let path = "", pen = false;
      vals.forEach((v, i) => { if (v == null) { pen = false; return; } path += (pen ? "L" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1); pen = true; });
      if (path) body += `<path d="${path}" fill="none" stroke="${s.color}" stroke-width="${s.w || 2}" stroke-linejoin="round" stroke-linecap="round"/>`;
      if (n <= 31) body += vals.map((v, i) => (v == null ? "" : `<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="${n <= 7 ? 3.5 : 2}" fill="${s.color}"/>`)).join("");
    }
    const legend = SER.map((s) => `<button class="lg${hiddenSeries.has(s.key) ? " off" : ""}" data-series="${s.key}"><i style="background:${s.color}"></i>${esc(s.name)}</button>`).join("");
    const gNote = !D.gLoaded ? (P === 7 ? "Cargando Google 7 días…" : "Google Trends no ha dado datos.") : D.web.every((v) => v == null) && D.yt.every((v) => v == null) ? "Google Trends no tiene datos de este periodo para esta palabra." : "";
    box.innerHTML = `<div class="tchart-wrap"><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">${grid}${xl}${body}
        <line id="tcur" x1="0" x2="0" y1="${pad.t}" y2="${H - pad.b}" class="cur" visibility="hidden"/><rect id="thit" x="${pad.l}" y="0" width="${W - pad.l - pad.r}" height="${H}" fill="transparent"/></svg>
      <div id="ttip" class="ttip" hidden></div></div>
      <div class="legend">${legend}</div>
      ${gNote ? `<p class="note">${esc(gNote)}</p>` : ""}
      <p class="note">⚠️ TikTok = <b>muestra de ${esc(D.N)} vídeos</b> publicados en estos ${P} días (de los ~${esc((r.sample || {}).n || 0)} analizados: los más populares del hashtag + la búsqueda), <b>no el total</b>. Todas las líneas van de 0 a 100: 100 = el día más alto del periodo (en TikTok, el día con más vídeos de la muestra; las barras, el día con más views). Toca la gráfica para ver los números de cada día.${P === 7 ? " Google 7 días viene por horas: se muestra la media de cada día (100 = la hora pico de la semana; el primer día puede estar incompleto)." : ""}</p>`;
    const hit = $("#thit"), tip = $("#ttip"), cur = $("#tcur"), svg = box.querySelector("svg");
    const show = (ev) => {
      const rc = svg.getBoundingClientRect(), x = ((ev.clientX - rc.left) / rc.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.floor((x - pad.l) / cw)));
      cur.setAttribute("x1", X(i)); cur.setAttribute("x2", X(i)); cur.setAttribute("visibility", "visible");
      const gv = (v) => (v == null ? "–" : Math.round(v));
      tip.innerHTML = `<b>${esc(dayLabel(D.days[i], true))}</b><div><i style="background:#4c8dff"></i>Google web: ${gv(D.web[i])}</div><div><i style="background:#ff4d4d"></i>YouTube: ${gv(D.yt[i])}</div>
        <div><i style="background:#25f4ee"></i>TikTok: <b>${D.tn[i]}</b> vídeo${D.tn[i] === 1 ? "" : "s"}</div><div><i style="background:#fe2c55"></i>Views de esos vídeos: <b>${num(D.tv[i])}</b></div>`;
      tip.hidden = false;
      const px = (X(i) / W) * rc.width, tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(rc.width - tw, px - tw / 2)) + "px";
    };
    hit.addEventListener("pointermove", show); hit.addEventListener("pointerdown", show);
    hit.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse") { tip.hidden = true; cur.setAttribute("visibility", "hidden"); } });
  }
  async function setPeriod(P) {
    period = P; localStorage.setItem("ttr_speriod", String(P));
    document.querySelectorAll("#tperiod button").forEach((b) => b.classList.toggle("on", Number(b.dataset.period) === P));
    if (!last) return;
    drawTrend(last);
    if (P === 7 && !last._t7) {
      const q = last.q, r = await api({ action: "trends", q, period: 7 });
      if (last && last.q === q) { last._t7 = (r && r.trends) || { web: { days: [] }, youtube: { days: [] } }; if (period === 7) drawTrend(last); }
    }
  }
  let rsz = null;
  window.addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => last && drawTrend(last), 200); });

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
    const pb = e.target.closest("#tperiod [data-period]");
    if (pb) { setPeriod(Number(pb.dataset.period)); return; }
    const lg = e.target.closest("[data-series]");
    if (lg) { const k = lg.dataset.series; hiddenSeries.has(k) ? hiddenSeries.delete(k) : hiddenSeries.add(k); last && drawTrend(last); return; }
    const u = e.target.closest("[data-unwatch]");
    if (u) { toggleWatch(u.dataset.unwatch, false); }
  });
  document.addEventListener("submit", (e) => { if (e.target.id === "sform") { e.preventDefault(); doSearch($("#sq").value); } });
  if (new URLSearchParams(location.search).get("buscar") || location.hash === "#buscador") {
    window.addEventListener("load", () => { const b = document.querySelector(".tabs button[data-tab=search]"); if (b) b.click(); });
  }
})();

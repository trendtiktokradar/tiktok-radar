// Función serverless de Vercel: pestaña "Buscador" del panel.
// Hace de puente entre el navegador y el servicio del box (collector/buscador.py). El navegador NUNCA ve la URL del box.
//
// La URL del box (quick tunnel de Cloudflare, cambia si el box se reinicia) la publica el propio box en
// box.json de la rama "feedback" del repo. Aquí se lee con la API de GitHub (sin caché; con RADAR_GH_TOKEN si existe)
// y, si falla, de raw.githubusercontent.com. Se guarda en memoria 60 s.
//
// Variables de entorno (Vercel → Settings → Environment Variables):
//   RADAR_PIN       el mismo PIN del botón "No es TikTok" (obligatorio)
//   RADAR_GH_TOKEN  opcional aquí (solo para leer box.json sin límite de peticiones)
// Opcionales: RADAR_REPO, RADAR_FEEDBACK_BRANCH, RADAR_BOX_URL (fija la URL del box, p. ej. pruebas en local)
//
// GET  /api/search                         -> { configured, box: "online"|"offline"|"unknown" }
// POST /api/search {pin, action:"check"}    -> 200 si el PIN es correcto (lo usa el box para validar el PIN)
// POST /api/search {pin, action:"search", q, fresh?} | {pin, action:"watch", q, on} | {pin, action:"watchlist"}
"use strict";
const crypto = require("crypto");

const REPO = process.env.RADAR_REPO || "trendtiktokradar/tiktok-radar";
const BRANCH = process.env.RADAR_FEEDBACK_BRANCH || "feedback";
const BOX_RX = /^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/;
let boxCache = { url: null, at: 0 };

function send(res, code, obj) {
  res.statusCode = code;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(obj));
}

function pinOk(given) {
  const want = process.env.RADAR_PIN || "";
  const a = crypto.createHash("sha256").update(String(given || "")).digest();
  const b = crypto.createHash("sha256").update(want).digest();
  return want.length > 0 && crypto.timingSafeEqual(a, b);
}

async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") { try { return JSON.parse(req.body); } catch (_) { return {}; } }
  const chunks = [];
  for await (const ch of req) chunks.push(ch);
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch (_) { return {}; }
}

async function fetchT(url, opts = {}, ms = 8000) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), ms);
  try { return await fetch(url, Object.assign({}, opts, { signal: c.signal })); } finally { clearTimeout(t); }
}

// URL actual del box: env fija > caché 60 s > API de GitHub > raw
async function boxUrl(force) {
  if (process.env.RADAR_BOX_URL) return process.env.RADAR_BOX_URL.replace(/\/$/, "");
  if (!force && boxCache.url && Date.now() - boxCache.at < 60000) return boxCache.url;
  const tries = [];
  const h = { Accept: "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tiktok-radar-vercel" };
  if (process.env.RADAR_GH_TOKEN) tries.push(["https://api.github.com/repos/" + REPO + "/contents/box.json?ref=" + BRANCH, Object.assign({ Authorization: "Bearer " + process.env.RADAR_GH_TOKEN }, h)]);
  tries.push(["https://api.github.com/repos/" + REPO + "/contents/box.json?ref=" + BRANCH, h]);
  tries.push(["https://raw.githubusercontent.com/" + REPO + "/" + BRANCH + "/box.json?t=" + Math.floor(Date.now() / 30000), {}]);
  for (const [u, headers] of tries) {
    try {
      const r = await fetchT(u, { headers }, 5000);
      if (!r.ok) continue;
      const j = JSON.parse(await r.text());
      if (j && typeof j.url === "string" && BOX_RX.test(j.url)) { boxCache = { url: j.url, at: Date.now() }; return j.url; }
    } catch (_) { /* siguiente */ }
  }
  return null;
}

async function callBox(path, pin, opts = {}, ms = 50000) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const base = await boxUrl(attempt > 0);
    if (!base) return { status: 503, body: { error: "box_offline", why: "sin_url" } };
    try {
      const r = await fetchT(base + path, Object.assign({}, opts, { headers: Object.assign({ "X-Radar-Pin": pin, "Content-Type": "application/json" }, opts.headers || {}) }), ms);
      const txt = await r.text();
      let body;
      try { body = JSON.parse(txt); } catch (_) { body = null; }
      if (body && typeof body === "object") return { status: r.status, body };
      // respuesta que no es del servicio (túnel caído → página de error de Cloudflare): reintentar con URL fresca
    } catch (e) {
      if (e && e.name === "AbortError") return { status: 504, body: { error: "box_timeout" } };
    }
    boxCache = { url: null, at: 0 };
  }
  return { status: 503, body: { error: "box_offline" } };
}

module.exports = async function handler(req, res) {
  const configured = !!process.env.RADAR_PIN;
  try {
    if (req.method === "GET") {
      if (!configured) return send(res, 200, { configured: false, box: "unknown" });
      const base = await boxUrl(false);
      let box = "offline";
      if (base) {
        try { const r = await fetchT(base + "/health", {}, 6000); const j = await r.json(); box = j && j.ok ? "online" : "offline"; } catch (_) { box = "offline"; boxCache = { url: null, at: 0 }; }
      }
      return send(res, 200, { configured: true, box });
    }
    if (req.method !== "POST") return send(res, 405, { error: "method" });
    if (!configured) return send(res, 503, { error: "not_configured" });
    const body = await readBody(req);
    if (!pinOk(body.pin)) {
      await new Promise((r) => setTimeout(r, 800));  // frena intentos de adivinar el PIN
      return send(res, 401, { error: "bad_pin" });
    }
    const pin = String(body.pin);
    if (body.action === "check") return send(res, 200, { ok: true });
    if (body.action === "search") {
      const q = typeof body.q === "string" ? body.q.replace(/\s+/g, " ").trim().slice(0, 60) : "";
      if (!q) return send(res, 400, { error: "bad_query" });
      const r = await callBox("/search?q=" + encodeURIComponent(q) + (body.fresh ? "&fresh=1" : ""), pin);
      return send(res, r.status, r.body);
    }
    if (body.action === "watch") {
      const q = typeof body.q === "string" ? body.q.trim().slice(0, 60) : "";
      if (!q) return send(res, 400, { error: "bad_query" });
      const r = await callBox("/watch", pin, { method: "POST", body: JSON.stringify({ q, on: !!body.on }) }, 15000);
      return send(res, r.status, r.body);
    }
    if (body.action === "watchlist") {
      const r = await callBox("/watchlist", pin, {}, 15000);
      return send(res, r.status, r.body);
    }
    return send(res, 400, { error: "bad_action" });
  } catch (e) {
    return send(res, 500, { error: "server_error" });
  }
};

// Función serverless de Vercel: botón "No es TikTok" del panel.
// Guarda las marcas en feedback.json de la rama "feedback" del repo (API Contents de GitHub).
// El collector lo lee en cada pasada: quita esas coins para todos los dispositivos y aprende del motivo.
//
// Variables de entorno (Vercel → Settings → Environment Variables):
//   RADAR_GH_TOKEN  token fine-grained de GitHub (solo este repo, Contents: Read and write)
//   RADAR_PIN       PIN que pide el panel una vez por dispositivo
// Opcionales: RADAR_REPO (por defecto trendtiktokradar/tiktok-radar), RADAR_FEEDBACK_BRANCH (feedback)
//
// GET  /api/not-tiktok            -> { configured, cas: [...], rules_off: [...], updated }
// POST /api/not-tiktok  {pin, action: "mark"|"unmark"|"rule_off"|"rule_on"|"check", items|cas|key}
"use strict";
const crypto = require("crypto");

const REPO = process.env.RADAR_REPO || "trendtiktokradar/tiktok-radar";
const BRANCH = process.env.RADAR_FEEDBACK_BRANCH || "feedback";
const FILE = "feedback.json";
const CA_RX = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const KEY_RX = /^[a-z_]{2,20}(:[a-z0-9 _.\-]{1,20})?$/;
const MAX_ITEMS = 60;

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

function gh(path, opts = {}) {
  return fetch("https://api.github.com/repos/" + REPO + path, Object.assign({}, opts, {
    headers: Object.assign({
      Authorization: "Bearer " + process.env.RADAR_GH_TOKEN,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "tiktok-radar-vercel",
    }, opts.headers || {}),
  }));
}

class GhError extends Error {
  constructor(code, status) { super(code); this.code = code; this.status = status; }
}

async function readDoc() {
  const r = await gh("/contents/" + FILE + "?ref=" + encodeURIComponent(BRANCH));
  if (r.status === 404) return { doc: emptyDoc(), sha: null };
  if (r.status === 401 || r.status === 403) throw new GhError("github_auth", r.status);
  if (!r.ok) throw new GhError("github_error", r.status);
  const j = await r.json();
  let doc;
  try { doc = JSON.parse(Buffer.from(j.content || "", "base64").toString("utf8")); } catch (_) { doc = emptyDoc(); }
  if (!doc || typeof doc !== "object") doc = emptyDoc();
  doc.marks = doc.marks && typeof doc.marks === "object" ? doc.marks : {};
  doc.rules_off = Array.isArray(doc.rules_off) ? doc.rules_off : [];
  doc.log = Array.isArray(doc.log) ? doc.log : [];
  return { doc, sha: j.sha };
}

function emptyDoc() { return { v: 1, updated: 0, marks: {}, rules_off: [], log: [] }; }

async function writeDoc(doc, sha, message) {
  const body = {
    message, branch: BRANCH,
    content: Buffer.from(JSON.stringify(doc, null, 1) + "\n", "utf8").toString("base64"),
    committer: { name: "tiktok-radar-bot", email: "tiktok-radar-bot@users.noreply.github.com" },
  };
  if (sha) body.sha = sha;
  const r = await gh("/contents/" + FILE, { method: "PUT", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
  if (r.ok) return true;
  if (r.status === 409 || r.status === 422) return false;  // otro cambio a la vez: reintentar
  if (r.status === 401 || r.status === 403) throw new GhError("github_auth", r.status);
  if (r.status === 404) throw new GhError("no_branch", r.status);
  throw new GhError("github_error", r.status);
}

const str = (v, n) => (typeof v === "string" ? v.slice(0, n) : null);

function applyAction(doc, body, now) {
  const action = body.action;
  if (action === "mark") {
    const items = (Array.isArray(body.items) ? body.items : []).filter((x) => x && CA_RX.test(x.ca || "")).slice(0, MAX_ITEMS);
    if (!items.length) return { error: "bad_request" };
    const g = String(now) + "-" + items[0].ca.slice(0, 6);  // grupo = una pulsación (coin + sus clones)
    for (const x of items) {
      doc.marks[x.ca] = {
        ts: now, g,
        name: str(x.name, 60), symbol: str(x.symbol, 30),
        keys: (Array.isArray(x.keys) ? x.keys : []).filter((k) => typeof k === "string" && KEY_RX.test(k)).slice(0, 8),
        dev: typeof x.dev === "string" && CA_RX.test(x.dev) ? x.dev : null,
      };
    }
    return { cas: items.map((x) => x.ca), msg: "No es TikTok: " + (str(items[0].name, 40) || items[0].ca) + (items.length > 1 ? " (+" + (items.length - 1) + ")" : "") };
  }
  if (action === "unmark") {
    const cas = (Array.isArray(body.cas) ? body.cas : []).filter((c) => typeof c === "string" && CA_RX.test(c)).slice(0, MAX_ITEMS);
    if (!cas.length) return { error: "bad_request" };
    for (const c of cas) delete doc.marks[c];
    return { cas, msg: "Sí es TikTok (deshacer): " + cas.length + " CA" };
  }
  if (action === "rule_off" || action === "rule_on") {
    const key = typeof body.key === "string" && KEY_RX.test(body.key) ? body.key : null;
    if (!key) return { error: "bad_request" };
    const set = new Set(doc.rules_off);
    if (action === "rule_off") set.add(key); else set.delete(key);
    doc.rules_off = [...set];
    return { key, msg: (action === "rule_off" ? "Regla desactivada: " : "Regla activada: ") + key };
  }
  return { error: "bad_action" };
}

async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") { try { return JSON.parse(req.body); } catch (_) { return {}; } }
  const chunks = [];
  for await (const ch of req) chunks.push(ch);
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch (_) { return {}; }
}

module.exports = async function handler(req, res) {
  const configured = !!(process.env.RADAR_GH_TOKEN && process.env.RADAR_PIN);
  try {
    if (req.method === "GET") {
      if (!configured) return send(res, 200, { configured: false });
      const { doc } = await readDoc();
      return send(res, 200, { configured: true, cas: Object.keys(doc.marks), rules_off: doc.rules_off, updated: doc.updated || 0 });
    }
    if (req.method !== "POST") return send(res, 405, { error: "method" });
    if (!configured) return send(res, 503, { error: "not_configured" });
    const body = await readBody(req);
    if (!pinOk(body.pin)) {
      await new Promise((r) => setTimeout(r, 800));  // frena intentos de adivinar el PIN
      return send(res, 401, { error: "bad_pin" });
    }
    if (body.action === "check") return send(res, 200, { ok: true });
    for (let attempt = 0; attempt < 4; attempt++) {
      const { doc, sha } = await readDoc();
      const now = Date.now();
      const out = applyAction(doc, body, now);
      if (out.error) return send(res, 400, { error: out.error });
      doc.v = 1; doc.updated = now;
      doc.log.push({ ts: now, action: body.action, cas: out.cas, key: out.key });
      doc.log = doc.log.slice(-300);
      if (await writeDoc(doc, sha, out.msg)) return send(res, 200, { ok: true, action: body.action, cas: out.cas || [], key: out.key || null });
      await new Promise((r) => setTimeout(r, 300 + attempt * 400));
    }
    return send(res, 409, { error: "conflict" });
  } catch (e) {
    return send(res, e instanceof GhError && e.code === "github_auth" ? 502 : 500, { error: e.code || "server_error", status: e.status || null });
  }
};

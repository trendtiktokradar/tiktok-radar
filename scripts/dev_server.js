// Servidor local para probar el panel CON las funciones /api (como en Vercel), sin instalar nada:
//   RADAR_PIN=1234 RADAR_BOX_URL=http://127.0.0.1:18790 node scripts/dev_server.js 8766
// Sirve web/ y ejecuta web/api/<nombre>.js para /api/<nombre>.
"use strict";
const http = require("http"), fs = require("fs"), path = require("path");
const WEB = path.join(__dirname, "..", "web");
const PORT = Number(process.argv[2] || 8766);
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml" };
http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://x");
  const m = u.pathname.match(/^\/api\/([a-z0-9-]+)$/);
  if (m) {
    const f = path.join(WEB, "api", m[1] + ".js");
    if (!fs.existsSync(f)) { res.statusCode = 404; return res.end("not found"); }
    req.query = Object.fromEntries(u.searchParams);
    try { return await require(f)(req, res); } catch (e) { res.statusCode = 500; return res.end(String(e)); }
  }
  let p = path.normalize(path.join(WEB, decodeURIComponent(u.pathname === "/" ? "/index.html" : u.pathname)));
  if (!p.startsWith(WEB) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.statusCode = 404; return res.end("not found"); }
  res.setHeader("Content-Type", TYPES[path.extname(p)] || "application/octet-stream");
  fs.createReadStream(p).pipe(res);
}).listen(PORT, "127.0.0.1", () => console.log("panel local en http://127.0.0.1:" + PORT));

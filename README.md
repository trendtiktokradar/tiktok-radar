# 📡 TikTok Radar (Solana)

Panel web que muestra **todas las memecoins de Solana relacionadas con TikTok** (trends, sonidos, memes,
creadores virales) para que Alex las vea aunque no esté delante del PC.

**No usa IA.** El programa son reglas fijas en Python: el día a día cuesta **0 tokens**.
El agente TikTok Radar solo se usa bajo demanda (analizar una coin concreta, afinar palabras clave,
arreglar algo si una fuente cambia).

---

## Cómo funciona

```
 cada 5 min (box)                          GitHub (rama "data")            Vercel (web estática)
┌──────────────────────┐   data.json     ┌────────────────────┐  fetch   ┌───────────────────────┐
│ collector/radar.py   │ ──────────────▶ │ data.json          │ ◀─────── │ index.html + app.js    │
│ (Python, sin IA)     │   state.json    │ state.json         │          │ (se despliega 1 vez)   │
└──────────────────────┘                 └────────────────────┘          └───────────────────────┘
```

### 1. Fuentes que consulta (todas públicas, sin login)

| Fuente | Qué aporta | Estado (probado 02/10/2026) |
|---|---|---|
| DexScreener · categoría **TikTok** (`/metas/meta/v1/tiktok`) | Lista curada por DexScreener de coins TikTok ("If it trends, it trades") | ✅ funciona |
| DexScreener · categoría **Brainrot** | Memes brainrot (nacidos casi siempre en TikTok) | ✅ funciona |
| DexScreener · búsqueda (`/latest/dex/search`) | Busca "tiktok", "fyp", "douyin"… + trends manuales + hashtags de Creative Center | ✅ funciona |
| DexScreener · perfiles, boosts, CTOs recientes | Coins nuevas con links (detecta links a tiktok.com) | ✅ funciona |
| DexScreener · `/tokens/v1/solana/...` | Refresca MC, liquidez, volumen, cambios de precio de todas las coins seguidas | ✅ funciona |
| pump.fun (`frontend-api-v3.pump.fun/coins`) | Todas las coins nuevas desde la última pasada (~25/min) + las que más tradean | ✅ funciona (tiene límite de peticiones; el programa reintenta) |
| TikTok Creative Center (`CreativeOne/KnowledgeAPI/GetHashtagList`) | Hashtags en tendencia por país | ⚠️ parcial: sin login solo da el **top 3 por país**; se cruzan 13 países × 7 y 30 días ≈ 60 hashtags. Canciones/vídeos piden login → no se usan |
| GMGN | – | ❌ bloqueado por Cloudflare, no se consulta (los links a GMGN del panel sí funcionan) |

Si una fuente falla, el programa sigue con las demás y lo marca en la pestaña **Fuentes** del panel.

### 2. Reglas para decidir si una coin "es de TikTok" (score)

| Motivo | Puntos |
|---|---|
| `tiktok` / `tik tok` / `fyp` / `foryou` / `douyin` / `抖音` en nombre o ticker | 3 |
| Link a `tiktok.com` en sus redes, web o descripción | 3 |
| Está en la categoría TikTok de DexScreener | 3 |
| Está en la categoría Brainrot de DexScreener | 2 |
| Menciona TikTok/fyp/douyin en la descripción | 2 |
| Nombre/ticker igual a un hashtag en tendencia de Creative Center | 2 |
| Nombre/ticker igual a un **trend manual** (`manual_trends`) | 2 |
| Frases tipo "went viral", "viral video", "trending sound" en la descripción | 1 (2 si hay varias) |

Se guarda la coin si suma **≥ 2** (`min_score` en `collector/config.json`).

### 3. Histórico
- Todo se guarda en `state/state.json`: las coins que salieron mientras no mirabas **siguen ahí**.
- En cada pasada se refrescan las métricas de todas las coins seguidas.
- Coin **muerta** = más de 48 h de vida, MC < $8K y volumen 24 h < $200. Las muertas se borran a los 7 días.
- Máximo 1.500 coins guardadas.

### 4. El panel (`web/`)
- Lista de coins con: nombre, ticker, **botón Copiar CA**, MC, liquidez, volumen 1h/24h, cambio 5m/1h/24h,
  momentum, edad, motivo (badge) y links a **GMGN, DexScreener, pump.fun, X, TikTok, web, Telegram**.
- Orden: recién detectadas, más jóvenes, MC, volumen 1h, volumen 24h, momentum, cambio 1h, score.
- Filtros: MC mín/máx, edad máx, liquidez mín, tipo de motivo, búsqueda libre.
- **Agrupar clones**: junta las coins con el mismo nombre (p. ej. 30 "Cornell") y muestra la de más MC con "+N clones".
- **Ocultar sin actividad** (activado por defecto): esconde clones con volumen casi cero.
- **NUEVA** / "Solo nuevas desde mi última visita": marca lo que apareció desde que abriste el panel la vez anterior.
- Pestaña **Trends TikTok**: hashtags de Creative Center con views, posts, gráfica de 7 días, si sube o baja y
  cuántas coins hay ya de ese trend (botón "ver").
- Se recarga sola cada 2 min. Funciona en el móvil.

**Momentum** = % del MC que se ha movido en volumen en la última hora + 0,3 × cambio de precio 1h.

---

## Uso en local

```bash
cd /workspace/tiktok-radar
python3 collector/radar.py                 # una pasada (≈40-80 s). Sin dependencias.
python3 -m http.server 8765 -d web          # abrir http://localhost:8765
```

Afinar sin tocar código → `collector/config.json`:
- `manual_trends`: memes que veas en TikTok (`["tung tung", "67", "pibble"]`); se buscan y se marcan.
- `search_terms`, `strong_patterns`, `desc_phrases`, `trend_stopwords`, `trend_countries`.
- `dexscreener_metas`: categorías de DexScreener que cuentan (p. ej. añadir `internet-animals`).

Captura de pantalla: `.venv/bin/python scripts/screenshot.py` (necesita `pip install playwright`).

---

## Publicación gratis (GitHub + Vercel) — recomendado

**Opción elegida: el box ejecuta el programa cada 5 min y sube SOLO los datos a una rama `data` de GitHub.
La web (Vercel) se despliega una sola vez y lee `data.json` directamente de GitHub.**

Por qué:
- Vercel Hobby tiene ~100 despliegues/día. Redesplegar en cada actualización (cada 5 min = 288/día) no cabe.
  Así Vercel solo despliega cuando cambia el código (y `web/vercel.json` le dice que ignore la rama `data`).
- `raw.githubusercontent.com` permite CORS y cachea 5 min → la web ve datos con ≤ 10 min de retraso.
  (jsDelivr cachea hasta 12 h: descartado.)
- pump.fun, DexScreener y Creative Center funcionan desde el box (probado). Desde los servidores de GitHub
  no está garantizado (pump.fun/TikTok suelen bloquear IPs de datacenter) y el cron de GitHub se retrasa.
- La rama `data` es **un único commit que se reemplaza** (force-push solo de esa rama), así el repo no crece.
- **Respaldo**: `.github/workflows/fallback-collector.yml` corre cada 15 min en GitHub y, solo si los datos
  tienen más de 20 min (box caído), ejecuta el collector él mismo. Gratis si el repo es público.

### Estado actual (02/10/2026)
- Repo público: https://github.com/trendtiktokradar/tiktok-radar (código en `main`, datos en la rama `data`).
- Datos publicados: https://raw.githubusercontent.com/trendtiktokradar/tiktok-radar/data/data.json
- El box ejecuta `scripts/loop.sh` (una pasada cada 5 min) y publica con el token de la variable de entorno
  `GITHUB_TOKEN_TIKTOK_RADAR` (se lee en el momento vía credential helper; nunca se guarda en disco ni en el repo).
- Respaldo: GitHub Action `fallback-collector` cada 15 min (solo actúa si los datos tienen > 20 min).

### Arrancar / parar el bucle en el box
```bash
cd /workspace/tiktok-radar
RADAR_PUBLISH=1 RADAR_REMOTE=https://github.com/trendtiktokradar/tiktok-radar.git \
  nohup setsid scripts/loop.sh >/dev/null 2>&1 &      # necesita GITHUB_TOKEN_TIKTOK_RADAR en el entorno
pkill -f tiktok-radar/scripts/loop.sh                  # parar
tail -f logs/radar.log                                  # ver qué hace
```
Si el box se reinicia, el bucle se para: hay que volver a lanzarlo (mientras, la Action de respaldo mantiene los datos).

### Conectar Vercel (una sola vez)
1. Abrir https://vercel.com/new/import?s=https://github.com/trendtiktokradar/tiktok-radar
   (o https://vercel.com/new → "Import Git Repository" → `trendtiktokradar/tiktok-radar`).
   Si no aparece el repo: "Adjust GitHub App Permissions" y dar acceso a `tiktok-radar`.
2. **Root Directory → `web`** (botón Edit). Framework Preset: **Other**. Build/Output: dejar vacío.
3. Deploy. La URL (tipo `tiktok-radar-xxx.vercel.app`) es el panel; se puede renombrar en Settings → Domains.
4. Vercel solo redesplegará cuando cambie el código en `main`; la rama `data` está ignorada (`web/vercel.json`).

Nota: GitHub desactiva los workflows programados tras 60 días sin actividad en el repo; si pasa, se reactiva con un clic.

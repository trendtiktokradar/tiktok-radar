# 📡 TikTok Radar (Solana)

Panel web que muestra **todas las memecoins de Solana relacionadas con TikTok** (trends, sonidos, memes,
creadores virales) para que Alex las vea aunque no esté delante del PC.

**No usa IA.** El programa son reglas fijas en Python: el día a día cuesta **0 tokens**.
El agente TikTok Radar solo se usa bajo demanda (analizar una coin concreta, afinar palabras clave,
arreglar algo si una fuente cambia).

---

## Cómo funciona

```
 cada 5 min (box)                          GitHub (rama "data")            Vercel (web estática + /api)
┌──────────────────────┐   data.json     ┌────────────────────┐  fetch   ┌───────────────────────┐
│ collector/radar.py   │ ──────────────▶ │ data.json          │ ◀─────── │ index.html + app.js    │
│ (Python, sin IA)     │   state.json    │ state.json         │          │ (se despliega 1 vez)   │
└──────────────────────┘                 └────────────────────┘          └───────────────────────┘
```

### 1. Fuentes que consulta (todas públicas, sin login)

| Fuente | Qué aporta | Estado (probado 02/10/2026) |
|---|---|---|
| DexScreener · categoría **TikTok** (`/metas/meta/v1/tiktok`) | Lista curada por DexScreener de coins TikTok ("If it trends, it trades") | ✅ funciona |
| DexScreener · categoría **Brainrot** | Solo badge informativo | ✅ funciona |
| DexScreener · `/orders/v1/solana/<CA>` | DEX PAID (perfil pagado) y boosts | ✅ funciona |
| DexScreener · búsqueda (`/latest/dex/search`) | Busca "tiktok", "tik tok", "fyp", "douyin"… + trends manuales | ✅ funciona (límite de peticiones estricto) |
| DexScreener · perfiles, boosts, CTOs recientes | Coins nuevas con links (detecta links a tiktok.com) | ✅ funciona |
| DexScreener · `/tokens/v1/solana/...` | Refresca MC, liquidez, volumen, cambios de precio de todas las coins seguidas | ✅ funciona |
| pump.fun (`frontend-api-v3.pump.fun/coins`) | Todas las coins nuevas desde la última pasada (~25/min) + las que más tradean | ✅ funciona (tiene límite de peticiones; el programa reintenta) |
| TikTok Creative Center (`CreativeOne/KnowledgeAPI/GetHashtagList`) | Hashtags en tendencia por país (solo informativo) | ⚠️ parcial: sin login solo da el **top 3 por país**; se cruzan 13 países × 7 y 30 días ≈ 60 hashtags. Canciones/vídeos piden login → no se usan |
| GMGN | – | ❌ bloqueado por Cloudflare, no se consulta (los links a GMGN del panel sí funcionan) |

Si una fuente falla, el programa sigue con las demás y lo marca en la pestaña **Fuentes** del panel.

### 2. Regla para que una coin entre (ESTRICTA, solo TikTok)

Una coin sale en el radar **solo si cumple al menos una** de estas tres condiciones:

1. Tiene un **link a tiktok.com** (vm.tiktok.com, vt.tiktok.com…) en sus redes, web o descripción.
2. **`tiktok` / `tik tok` / `fyp` / `douyin` (抖音)** aparece en el nombre, el ticker o la descripción.
3. Está en la **categoría TikTok de DexScreener**.

Badges **solo informativos** (se muestran punteados, NUNCA bastan para entrar): hashtag de Creative Center,
categoría Brainrot de DexScreener, frases tipo "went viral"/"viral video", trends manuales.

Las reglas se vuelven a aplicar en cada pasada a todo lo guardado, así que si se cambian, el histórico se re-filtra solo.

Encima de esta regla actúa el botón **🚫 No es TikTok** (ver sección 6): lo marcado se quita siempre y el filtro
aprende de los motivos de entrada.

### 3. Solo coins de menos de 24 horas
- Edad = creación del par más antiguo en DexScreener o `created_timestamp` de pump.fun (lo que sea anterior).
- Todo lo que tenga más de 24 h (`max_age_hours` en `config.json`) se borra del histórico y de `data.json`.
- Si no se conoce la fecha de creación en 15 min, también se descarta (no se puede garantizar < 24 h).
- Las coins que salieron mientras no mirabas siguen ahí hasta cumplir 24 h.

### 4. DEX PAID
- Por cada coin se consulta `https://api.dexscreener.com/orders/v1/solana/<CA>`.
  Respuesta real: `{"orders":[{"type":"tokenProfile","status":"approved","paymentTimestamp":…}],"boosts":[…]}`.
- **DEX PAID** = hay un pedido `tokenProfile` con estado `approved`. Si está `processing` sale "DEX en revisión".
- Caché por CA en `state.json`: una vez pagado ya no se vuelve a consultar; las no pagadas se re-consultan cada
  10 min (máx. 40 consultas por pasada; el límite de DexScreener es 60/min).
- Badge ⚡ = boosts activos ahora mismo en DexScreener.
- En el panel el badge **DEX PAID** es verde y lleva el icono de DexScreener (`web/img/dexscreener.png`, copia local,
  sin hotlink). El filtro "Solo DEX PAID" sigue igual.

### 5. El panel (`web/`)
- Lista de coins con: nombre, ticker, **botón Copiar CA**, MC, liquidez, volumen 1h/24h, cambio 5m/1h/24h,
  momentum, edad, motivo (badge), **DEX PAID** y links a **GMGN, DexScreener, pump.fun, X, TikTok, web, Telegram**.
- Orden por defecto: **más nuevas (creación)**. También: recién detectadas, MC, volumen 1h/24h, momentum, cambio 1h, score.
- Filtros: MC mín/máx, edad máx, liquidez mín, búsqueda libre, **Solo DEX PAID**, **Solo TikTok dev 🔥**.
- **🚫 No es TikTok** (botón naranja junto a Ocultar): quita la coin (y sus clones) en **todos** los dispositivos
  y enseña al filtro. Aviso con "Deshacer" y botón "Sí es TikTok" en la pestaña **Aprendido**.
- **TikTok dev 🔥 (N)**: el dev de la coin ha creado N coins TikTok en los últimos 7 días (pulsa el badge para ver cuáles).
  Link "Dev" a su perfil de pump.fun.
- **Ocultar**: botón en cada coin (si está agrupada, oculta también sus clones). Se guarda en el navegador
  (localStorage, cada dispositivo por separado). "Ver ocultas" muestra las ocultas con el botón "Mostrar de nuevo".
- **Agrupar clones**: junta las coins con el mismo nombre y muestra la de más MC con "+N clones".
- **Ocultar sin actividad** (activado por defecto): esconde coins con más de 1 h de vida, volumen 24 h < $200 y MC < $8K.
- **NUEVA** / "Solo nuevas desde mi última visita": lo que apareció desde que abriste el panel la vez anterior.
- Pestaña **Trends** (solo informativa), pestaña **Aprendido** (reglas aprendidas, coins marcadas, TikTok devs 🔥)
  y pestaña **Fuentes** (estado de cada fuente).
- Se recarga sola cada 2 min. Funciona en el móvil.

**Momentum** = % del MC que se ha movido en volumen en la última hora + 0,3 × cambio de precio 1h.

### 6. Botón "No es TikTok" + aprendizaje (sin IA)
```
 panel (botón) ──POST + PIN──▶ /api/not-tiktok (Vercel) ──API Contents──▶ rama "feedback": feedback.json
                                                                                  │
 collector (cada 5 min) ◀───────────── lee feedback.json (API de GitHub) ─────────┘
```
- La función `web/api/not-tiktok.js` (Node, sin dependencias) guarda en `feedback.json` de la rama **`feedback`**:
  CA(s), nombre, motivos de entrada (`keys`), wallet del dev y hora. La rama `feedback` no se despliega
  (`vercel.json` propio + `deploymentEnabled.feedback=false`) y **no** la toca el force-push de la rama `data`.
- El panel pide un **PIN** la primera vez en cada dispositivo (se guarda en localStorage) y oculta la coin al momento.
  También lee `GET /api/not-tiktok` para ocultar al instante lo marcado desde otro dispositivo.
- El collector, en cada pasada: quita todas las CAs marcadas y cuenta, por **motivo de entrada**
  (`kw_name:tiktok`, `kw_desc:tiktok`, `kw_name:fyp`, `link_tiktok`, `meta_tiktok`…), cuántas pulsaciones hubo de
  coins que entraron **solo** por ese motivo (una coin con sus clones cuenta 1). Si un motivo llega a
  `learning.min_marks` (3), deja de aceptar coins que entren **únicamente** por él.
- **Protegidos** (`learning.protected_keys`): `'tiktok' en el nombre`, `douyin` en el nombre y `link de TikTok`
  nunca se bloquean solos: son la base del radar y bloquearlos vaciaría casi todo. Lo que sí puede aprender:
  "tiktok/fyp/douyin solo en la descripción", "fyp en el nombre" y "solo categoría TikTok de DexScreener".
- En **Aprendido** se ve cada regla (marcas, activa/aprendiendo/protegida), lo que quita ahora mismo, y se puede
  **Desactivar** una regla o decir **Sí es TikTok** a una coin (vuelve en la siguiente pasada).
- Sin `RADAR_GH_TOKEN`/`RADAR_PIN` en Vercel el botón solo oculta en ese dispositivo y avisa
  "Falta configurar el token en Vercel".

### 7. Dev y "TikTok dev 🔥"
- Wallet creadora: campo `creator` de pump.fun; para coins vistas solo en DexScreener se consulta
  `frontend-api-v3.pump.fun/coins-v2/<CA>` (máx. 15 por pasada, caché por CA).
- Cuenta de coins TikTok por dev en los últimos `dev_hot.window_days` (7) días = historial propio del radar
  (`state.json → dev_history`) + coins creadas por ese dev en pump.fun (`/coins-v2/user-created-coins/<dev>`,
  máx. 8 devs por pasada, cada uno se re-mira cada 60 min) que cumplen la misma regla TikTok.
- Si llega a `dev_hot.min_coins` (3, contando la actual) sale **TikTok dev 🔥 (N)**. Solo informativo, **no bloquea**.

---

## Uso en local

```bash
cd /workspace/tiktok-radar
python3 collector/radar.py                 # una pasada (≈40-80 s). Sin dependencias.
python3 -m http.server 8765 -d web          # abrir http://localhost:8765
```

Afinar sin tocar código → `collector/config.json`:
- `max_age_hours`: edad máxima (24).
- `strong_patterns`: las palabras que hacen entrar una coin (tiktok, fyp, douyin).
- `dexscreener_metas`: categorías de DexScreener; `"qualifies": true` = cuenta para entrar (solo TikTok).
- `manual_trends`: memes que veas en TikTok; se buscan y salen como badge (no bastan para entrar).
- `search_terms`, `desc_phrases`, `trend_countries`, `dexpaid_recheck_minutes`, `dexpaid_max_checks_per_run`.
- `learning`: `min_marks` (3), `protected_keys`, `enabled`, repo/rama/fichero del feedback.
- `dev_hot`: `min_coins` (3), `window_days` (7), límites de consultas a pump.fun.

Probar el collector sin tocar los datos reales: `RADAR_STATE=/tmp/s.json RADAR_DATA=/tmp/d.json python3 collector/radar.py`
(con `RADAR_FEEDBACK_FILE=/ruta/feedback.json` usa un feedback local en vez del de GitHub).

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

### Activar el botón "No es TikTok" (una sola vez)
**A. Token de GitHub (cuenta `trendtiktokradar`)**
1. https://github.com/settings/personal-access-tokens/new (Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token).
2. Nombre `radar-vercel`, Expiration: la que quieras (p. ej. 1 año; al caducar el botón avisará y hay que crear otro).
3. Resource owner: `trendtiktokradar`. Repository access: **Only select repositories** → `trendtiktokradar/tiktok-radar`.
4. Permissions → Repository permissions → **Contents: Read and write** (Metadata: Read-only se pone sola). Nada más.
5. Generate token y copiarlo (empieza por `github_pat_`; solo se ve una vez).

**B. Variables en Vercel**
1. Proyecto → Settings → Environment Variables.
2. `RADAR_GH_TOKEN` = el token, entorno **Production** → Save.
3. `RADAR_PIN` = un PIN (mejor 6+ cifras) → Production → Save.
4. Deployments → último despliegue de Production → ⋯ → **Redeploy** (las variables solo se aplican en despliegues nuevos).
5. Abrir el panel, pulsar "No es TikTok" en una coin, meter el PIN. Pestaña **Aprendido** → debe salir en "Pendientes".

Nota: GitHub desactiva los workflows programados tras 60 días sin actividad en el repo; si pasa, se reactiva con un clic.

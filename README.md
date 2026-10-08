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
  10 min (máx. 40 consultas por pasada, 1/s; el límite de DexScreener es 60/min).
- Atajo: una coin que aparece en `/token-profiles/latest/v1` (perfil nuevo, no CTO) ya está pagada y se marca DEX PAID
  sin esperar a su turno de `/orders`. La lista la miran la pasada y el vigilante rápido (cada ~45 s, sección 9), que
  además deja sus resultados de `/orders` en `state/fastwatch.json` para que la pasada los use.
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
- **Filtro anti-rug** (`collector/antirug.py`, sin IA): en cada pasada se piden a la API web pública de Jupiter
  (`datapi.jup.ag/v1/assets/search`, sin login, ≤ 100 CAs por llamada, ~2 s para todo el radar) las **fees totales
  pagadas** por los traders (SOL, como el "Fees" de Axiom/GMGN), holders, traders y volumen. Una coin que cumpla
  alguna regla **desaparece de la web** (Coins, Top volumen, clones y sumas de grupo):
  - 🧹 `fake_chart`: MC ≥ $10K y fees < 1 SOL por cada $30K de MC (chart/MC falso).
  - 🤖 `wash`: volumen de toda su vida ≥ $5K y fees en USD < 0,3 % del volumen (volumen de bots; lo normal es ~0,5-1 %).
  - 💀 `rug`: ATH ≥ $30K y MC < 10 % del ATH.
  Si Jupiter falla (o no trae la coin) no se filtra, salvo el respaldo de 🤖 con DexScreener: operación media < $15
  con ≥ 1000 transacciones en 24 h. Para auditar falsos positivos: en `state/state.json` cada coin filtrada lleva
  `rf` (motivos), `rf_since` y `jup` (datos de Jupiter); `state.antirug_log` guarda las últimas 500 filtradas (con MC,
  ATH, fees y volumen al filtrarlas) y `state.antirug_last` / `data.json → antirug` el resumen de la pasada; el log
  de la pasada (`logs/radar.log`) dice cuántas por regla y cuáles son nuevas. **Avisos de Telegram:** justo antes de
  enviar se consulta Jupiter para esa coin; si cumple 🧹 o 🤖 no se avisa (se apunta una vez en el log y en
  `state/telegram.json → filtered`); 💀 no cuenta para avisos; si Jupiter falla, se avisa igual.
- Pestaña **🔥 Top volumen**: las 20 coins (mismas coins TikTok < 24 h, sin las ocultas ni las "No es TikTok") con más
  volumen en USD en **1h / 3h / 6h / 8h**, clones sumados en un grupo (sale la principal con "+N clones"), con compras 🟢
  y ventas 🔴 (nº de transacciones), MC, edad y badges DEX PAID / TikTok dev 🔥; al tocar una fila se abre la tarjeta
  completa. 1h y 6h = `volume.h1/h6` y `txns` de DexScreener. 3h y 8h = **historial propio**: en cada pasada se guarda en
  `state/state.json` (campo `vh` por coin, NO va a data.json) el volumen/compras/ventas `h24` del par (como la coin
  tiene < 24 h, es lo acumulado desde que nació el par), un punto cada ≥ 10 min de las últimas ~8,5 h; la ventana es
  "acumulado ahora − acumulado hace W h" (si se gradúa y cambia de par, se suman los dos). En data.json solo van las
  ventanas calculadas (`vw: {"3": [vol, compras, ventas, min_cubiertos, parcial], "8": …}`) y `vol_hist_since`.
  Coin más joven que la ventana = toda su vida. Si el historial no cubre la ventana (recién empezado o hueco),
  sale ◔ "cobertura parcial" y un aviso arriba.
- Pestaña **Buscador** (sección 8): escribes una palabra y el box te dice cuántos vídeos de TikTok hay, views, recientes vs antiguos, si sube o baja y los vídeos top.
- Pestaña **Trends** (solo informativa), pestaña **Aprendido** (reglas aprendidas, coins marcadas, TikTok devs 🔥)
  y pestaña **Fuentes** (estado de cada fuente).
- Se recarga sola cada 2 min. Funciona en el móvil.

**Momentum** = % del MC que se ha movido en volumen en la última hora + 0,3 × cambio de precio 1h.

### ATH (market cap máximo)
- **ATH = máx(** MC más alto visto por el radar en cualquier pasada (`ath_seen`, guardado en `state.json`),
  `ath_market_cap` de pump.fun (USD), MC actual **)**.
- pump.fun se consulta con `/coins-v2/<CA>` rotando (config `ath`: máx. 20 coins por pasada, cada coin cada 20 min;
  las que pump.fun no conoce se reintentan cada 6 h). Las coins que salen en los listados de pump.fun lo traen gratis.
- Se muestra en la tarjeta (junto al ticker) y en cada fila de clon; la lista de clones va ordenada por ATH
  (el más alto primero) y los clones con DEX PAID salen en verde. Al pasar el dedo/ratón sobre "ATH" dice la fuente.
- Fiabilidad: con dato de pump.fun es el ATH real de pump.fun (incluye picos entre pasadas). Si solo hay dato del radar,
  es el máximo de muestras cada 5 min desde que la coin entró: un pico corto entre pasadas o anterior a la detección no sale.

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
  `frontend-api-v3.pump.fun/coins-v2/<CA>` (con caché por CA).
- Regla (estricta, desde 02/10/2026): un dev sale como **TikTok dev 🔥 (N)** solo si en los últimos
  `dev_hot.window_days` (7) días:
  1. tiene **≥ `min_coins` (3) coins TikTok con nombre distinto** (clones y relanzamientos con el mismo nombre cuentan 1).
     Solo cuentan señales fuertes: link de TikTok o tiktok/fyp/douyin en nombre/ticker; nunca solo descripción ni solo categoría;
  2. **≥ `min_share` (50 %)** de todas las coins que creó en esos días son TikTok;
  3. su lista de pump.fun cubre la semana. pump.fun solo enseña ~250 coins por dev y **no en orden cronológico**:
     - con **< `max_created` (250)** coins en total se ve todo;
     - con más, se evalúa con las 250 visibles si entre ellas hay coins de antes de los 7 días (si todas las visibles
       son de esta semana, lanza > 250/semana = **lanzador masivo**, no se marca). Como las ocultas podrían ser de
       esta semana, además se exige el **peor caso**: TikTok ÷ (coins de 7 días visibles + todas las ocultas) ≥ 50 %.
       Ej.: 9VhX… (253 coins, 3 ocultas): 40 de 43 = 93 %, peor caso 40 de 46 = 87 % → sí. Un dev con 932 coins
       y 2 TikTok de 3 visibles esta semana: peor caso 0,3 % → no.
- Fuente: `/coins-v2/user-created-coins/<dev>` (hasta `scan_max_pages` = 5 páginas de 50, cada dev se re-mira cada
  60 min, máx. 8 devs por pasada) + coins TikTok del propio radar que no salgan en esa lista.
- Motivo del cambio: con la regla antigua (≥ 3 coins TikTok, sin mirar el total) salían bots que lanzan cientos de coins
  y copian todo lo viral (p. ej. 5 TikTok de 873 coins = 2 %). Con datos reales del 02/10: de 10 devs marcados quedaron 4.
- Solo informativo, **no bloquea**. El badge dice al pasar por encima cuántas coins TikTok distintas y qué % del total.

### 8. Buscador (pestaña "Buscador") — sin IA, sin login de TikTok
Escribes una palabra (o `#hashtag`) y en ~5-10 s sale: nº de vídeos y views del hashtag, recientes vs antiguos,
views/likes típicos y máximos, si sube o baja (Google Trends), vídeos top con link, hashtags relacionados y un
veredicto **🔥 HOT / 📈 sube / 📉 baja / 💤 flojo**.

```
 navegador ──POST {pin,q}──▶ /api/search (Vercel) ──X-Radar-Pin──▶ quick tunnel Cloudflare ──▶ collector/buscador.py (box)
                                   │  lee la URL del box de                                         │  Chrome headless propio (sin login)
                                   └─ box.json (rama "feedback") ◀── la publica el box al arrancar ─┘  + Google Trends + tikwm
```
**Fuentes (probadas 02/10/2026):**
| Fuente | Qué da | Notas |
|---|---|---|
| TikTok `/api/challenge/detail` | Vídeos y views totales del hashtag (`statsV2`) | TikTok firma las peticiones (X-Bogus/X-Gnarly) con su JS: por eso se piden con `fetch()` desde una pestaña de tiktok.com abierta en Chrome headless. Con curl/Vercel devuelve vacío |
| TikTok `/api/challenge/item_list` | 6 páginas × 30 vídeos del hashtag (fecha, views, likes, comentarios, shares) | Ordenados por **popularidad**, no por fecha. Páginas 2-6 en paralelo |
| TikTok `/api/search/general/full` | 4 páginas (~48) de la búsqueda normal | Solo cuentan los que mencionan la palabra (la búsqueda es difusa). La pestaña "Vídeos" con filtros de fecha pide login (403) |
| Google Trends (`pytrends`) | Curva diaria 90 días, web y YouTube (0-100) | No hay TikTok en Trends; Google limita si se abusa (caché 6 h por palabra) |
| tikwm.com `/api/challenge/search` | Hashtags parecidos con nº de vídeos; respaldo del detalle | No oficial, puede caer; su búsqueda de vídeos está bloqueada por Cloudflare |

**Gráfica "Tendencia" (7 / 30 / 90 días, botones):** en la misma gráfica (0-100, toca o pasa el ratón para ver
los números de cada día):
- **Google web** y **YouTube** (Google Trends). 30 y 90 días salen de la misma curva diaria de 90 días (30 = recorte
  reescalado para que el pico del periodo sea 100); 7 días se pide aparte por horas (`now 7-d`) y se muestra la
  media de cada día en la escala de Google (100 = la hora pico de la semana). Caché 6 h por palabra y periodo;
  la de 7 días solo se pide al pulsar "7 días".
- **TikTok vídeos/día** (línea) y **TikTok views/día** (barras): de la **muestra** (6 páginas del hashtag + 4 de la
  búsqueda ≈ 150-230 vídeos, páginas en paralelo, ~3-8 s), cuántos vídeos se publicaron cada día (hora de Madrid)
  y la suma de sus views. 100 = el día con más vídeos (o más views) del periodo; el tooltip da los números reales.
  **Es una muestra, no el total**: TikTok da los vídeos del hashtag por popularidad y la búsqueda prioriza lo
  reciente, así que sirve para ver la forma (cuándo arranca), no para contar.

**🆕 Últimos vídeos (arriba del todo):** los 20 vídeos **más nuevos de la muestra, ordenados por fecha** (fecha y
hora de Madrid, "hace X", views, likes y link; 8 visibles + "Ver más"). TikTok sin sesión **no deja ordenar por
fecha** (probado: ignora `publish_time`/`sort_type`), así que puede haber vídeos más recientes que no salen; la tarjeta
lo explica y enlaza a la búsqueda de TikTok. Si el hashtag es pequeño (TikTok rellena con vídeos ajenos: p. ej.
1 vídeo real y 66 devueltos), solo se quedan los que mencionan la palabra.

**Si TikTok no da vídeos:** sale un aviso claro "TikTok no devolvió vídeos para esta palabra" con el motivo
(TikTok falló / no existe el hashtag y la búsqueda no la menciona / hashtag sin vídeos visibles) y debajo igualmente
Google web y YouTube. Si fue un fallo de TikTok el veredicto es **⚠️ sin TikTok** (nunca "flojo") y no se guarda en
caché. **Auto-arreglo:** TikTok a veces deja "marcada" la sesión de Chrome y responde 403 o 200 vacíos (o la página
del hashtag no carga y no hay plantilla de `item_list`); recargar la página no lo arregla. Por eso, si una búsqueda
falla sin vídeos del hashtag, el box **reinicia Chrome y reintenta una vez** en la misma búsqueda; si ya se reinició
hace < 15 min o sigue sin plantilla, **borra el perfil** (`~/.tiktok-radar-chrome`, sin sesión: no se pierde nada) y
arranca limpio. Con 3 fallos seguidos también reinicia. Cada búsqueda se apunta en `logs/buscador.log` con estado de
TikTok (ok / vacio / fallo, reintentado), nº de vídeos y errores; `/health` enseña `tiktok.last_ok_s_ago`,
`fails_in_row`, `restarts` y `template`.

**🚀 Primer vídeo viral:** el vídeo **más antiguo de la muestra con ≥ 100K views** (`BUSCADOR_VIRAL_VIEWS`), con fecha,
views, link y "lleva X días". Si ninguno llega, sale el más visto. **Inicio de la subida:** suma móvil de 7 días de
vídeos/día de la muestra; base = mediana de esa suma entre 120 y 35 días atrás; si hoy o ayer supera
`max(5, 2 × base)`, se va hacia atrás mientras siga por encima y el primer día con vídeos de ese tramo es "empezó a
subir el …". Si no, "no se ve una subida reciente". Aproximado (misma muestra).

**Veredicto (reglas fijas, `verdict()` en `collector/buscador.py`).** Puntos:
- % de la muestra publicado en los últimos 7 días: ≥ 25 % → **+2**; ≥ 10 % → **+1**.
- ≥ 3 vídeos de las últimas 24 h en la muestra → **+1**.
- El vídeo más visto de los últimos 7 días: ≥ 1M views → **+2**; ≥ 100K → **+1**.
- Google Trends (el mejor de web/YouTube; media últimos 7 días ÷ media de las 4 semanas anteriores): ≥ ×2 → **+2**;
  ≥ ×1,3 → **+1**; ≤ ×0,77 → **−1**. Si casi nadie lo busca (media < 5) no cuenta (sería ruido).
- Watchlist: el hashtag crece ≥ 2 % de vídeos al día → **+1**.

**≥ 5 = 🔥 HOT · 3-4 = 📈 sube** · si Trends baja, o < 10 % de la muestra es de esta semana (y Trends no sube) = **📉 baja** ·
resto = **💤 flojo** · sin hashtag ni vídeos = **❔ sin datos** · TikTok falló = **⚠️ sin TikTok**.

**Watchlist (⭐ Seguir):** el box guarda una foto al día de cada palabra seguida (vídeos, views del hashtag, nº de
vídeos de la semana) en `state/buscador.json` y en el resultado sale la curva y el % de crecimiento por día
(máx. 25 palabras). Lo hace el propio servicio (revisa cada 15 min si a alguna le toca foto; 1 petición ligera
a TikTok por palabra y día). Sin IA.

**Límites y protección:** caché 45 min por palabra (↻ Actualizar fuerza una búsqueda nueva), como mucho 1 búsqueda
nueva cada 4 s y 3 en cola, 45 s máximo por búsqueda (si una fuente falla, sale el resto y se avisa).
Cada petición lleva el PIN: Vercel lo comprueba y el box lo vuelve a comprobar preguntando a `/api/search`
(`action: "check"`), así que el box **no guarda el PIN** (solo un hash en memoria 1 h). 10 PIN malos en 1 h → bloqueo.
La URL del box está en `box.json` (repo público), pero sin PIN no responde nada salvo `/health`.

**Por qué quick tunnel + box.json:** el box no tiene IP pública. `cloudflared tunnel --url` es gratis y sin cuenta,
pero su URL `*.trycloudflare.com` cambia en cada arranque. El servicio la publica en `box.json` de la rama
`feedback` (que no despliega) con la API de GitHub y `GITHUB_TOKEN_TIKTOK_RADAR`; `/api/search` la lee con la
API de GitHub (sin caché; con `RADAR_GH_TOKEN` si está) y la guarda 60 s. Si el túnel deja de responder desde
fuera 3 veces seguidas, el servicio lo reinicia y publica la URL nueva. Un túnel con nombre fijo necesitaría
cuenta de Cloudflare y un dominio propio.

**Limitaciones honestas:** los totales son del **hashtag** (`#palabra` sin espacios); para frases solo hay la muestra.
"Recientes vs antiguos", la línea TikTok y el primer viral se miden sobre ~150-230 vídeos (los más populares + búsqueda), no sobre todos. No hay likes
totales del hashtag. TikTok no da su gráfica de tendencia sin login → se usa Google Trends + la watchlist.
Si TikTok endurece el anti-bot o cambia su API, la parte TikTok puede fallar (sale el resto).
Depende de que el box esté encendido.

**Activar (una vez):** en Vercel basta `RADAR_PIN` (el mismo que "No es TikTok") y Redeploy. Sin él la pestaña
avisa "Falta configurar el PIN en Vercel".

### 9. Avisos de Telegram (@tiktokradarmyxd_bot)
- **Solo desde el box** (en GitHub Actions están desactivados para no duplicar). Token en la variable de entorno
  `TELEGRAM_BOT_TOKEN_TIKTOK_RADAR` (nunca en el repo ni en logs). Los mandan dos procesos que comparten el registro de
  avisos enviados (`state/telegram.json`, con candado de fichero: nunca se avisa dos veces):
  - el **vigilante rápido** `collector/fastwatch.py` (cada ~45 s, aparte de la pasada): avisa en cuanto lo detecta;
  - la pasada lenta (~8 min), como red de seguridad.
- **Chat**: el primer chat privado que escribe al bot (/start) queda guardado en `state/telegram.json` (fuera del repo,
  no se publica) y solo se le escribe a él. Mensaje de bienvenida "✅ TikTok Radar conectado".
- **Solo coins con un link de TikTok asociado** (en web/redes/descripción): vídeo o foto (`tiktok.com/@x/video/ID`,
  `/photo/ID`), enlace corto (`vm.`/`vt.tiktok.com/…`, `tiktok.com/t/…`) o perfil (`tiktok.com/@cuenta`).
  **No cuentan**: la cuenta oficial `@tiktok` (y otras oficiales; `alerts.excluded_accounts` añade más), búsquedas, tags,
  música…, ni el nombre/ticker ("TikTok Coin" sin link no avisa), la descripción, la categoría o "fyp" (esas coins
  siguen en el panel). Nunca las marcadas "No es TikTok".
- **Solo eventos frescos**: de hace ≤ `alerts.fresh_minutes` (30). Lo más antiguo (p. ej. una coin que el radar
  descubre horas después de pagar) se da por visto sin avisar. En DEX PAID cuenta la aprobación: max(hora del pago,
  última comprobación en la que aún no estaba pagada) o el momento en que aparece su perfil nuevo.
- **Avisos** (una vez por coin y tipo):
  - 💰 **DEX PAID**: la coin pasa a tener DEX pagado (`/orders` de DexScreener).
  - 🎓 **BONDING real**: el par principal en DexScreener es un AMM (pumpswap, raydium, meteora…) con pool de hace ≤ 30 min
    y además pump.fun dice `complete=true` **o** el pool se creó después de que el radar viera la coin en bonding curve
    (`curve_seen`). Un pool que ya existía desde el lanzamiento no cuenta (evita falsos "bonding" de meteoradbc).
  - Texto: nombre, $ticker, CA (toca para copiar), MC, ATH, liquidez, edad, hora del pago/migración y hace cuánto,
    por qué es TikTok (con el link al vídeo),
    🔥 TikTok dev si aplica y links a DexScreener / pump.fun / GMGN / panel. Sin vista previa de links.
- Al conectar por primera vez, lo que ya estaba pagado/graduado se da por visto (solo se avisa si pasó en los últimos 15 min).
- ~1 mensaje/s, máx. `alerts.max_per_run` (15) por pasada (el resto en la siguiente). Si Telegram falla, la pasada sigue.
- Comandos (los responde el vigilante rápido en ≤ ~1 min): `/estado`, `/pausa` (lo que pase en pausa se da por visto),
  `/reanudar`.
- **Vigilante rápido** (`collector/fastwatch.py`, `scripts/fastwatch.sh start|stop|status|ensure`, log `logs/fastwatch.log`).
  En cada ciclo de ~45 s (la lista de perfiles, cada 20 s):
  1. `/token-profiles/latest/v1` (cada 20 s, también entre ciclos; ≈ 3/min): perfil nuevo no CTO = DEX PAID. Si la coin tiene link de TikTok (seguida por
     el radar, o nueva: nombre/edad con `/tokens/v1`, < 24 h) confirma la hora con `/orders` y avisa al momento.
  2. Respaldo: `/orders` rotando por las coins con link de TikTok sin pagar (6 por ciclo, 1/s ≈ 8/min; va después del paso 3 porque es lo menos urgente; jóvenes < 3 h cada
     5 min, el resto cada 20 min). Se pausa mientras la pasada hace sus `/orders` → entre los dos ≤ 60/min.
  3. BONDING: `/tokens/v1` en lotes de 30 (las 120 de más MC en bonding curve cada ciclo + 60 del resto rotando): el par
     principal pasa de pumpfun/meteoradbc… a un AMM. Y pump.fun `/coins-v2` (`complete`) para las de MC ≥ 35K.
  4. Graduaciones instantáneas: pump.fun `/coins?complete=true&sort=created_timestamp&order=DESC` (1 petición):
     coins creadas hace ≤ 30 min que ya completaron la curva (el radar no llega a verlas en curva). Si tienen link de
     TikTok en website/twitter/telegram/descripción → aviso BONDING en ≤ ~1 min.
  - Si DexScreener responde 429 respeta su `Retry-After` en todos sus endpoints (el límite es por IP y común; el box
    comparte IP de salida, así que a veces llegan 429 aunque el radar vaya despacio). Lee las coins de `state/state.json` (lo escribe la pasada) y
    deja lo que ve en `state/fastwatch.json` (latido, contadores de peticiones, últimos avisos con su latencia).
  - Config `fastwatch` en `config.json`. `scripts/loop.sh` hace `scripts/fastwatch.sh ensure` en cada vuelta: si está
    caído o sin latido > 5 min, lo (re)arranca. `FASTWATCH=0` lo desactiva.
- Config `alerts` en `config.json`: `enabled`, `dex_paid`, `bonding`, `min_mc` (0 = sin mínimo), `max_per_run`.
- Prueba: `python3 collector/alerts.py --test` (manda un aviso real marcado "(prueba)").
- Para cambiar de chat: borrar `chat_id` de `state/telegram.json` y escribir /start al bot desde el chat nuevo.

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

Buscador en local (sin túnel ni GitHub):
```bash
.venv/bin/pip install -r requirements-dev.txt          # playwright, aiohttp, pytrends (usa /usr/bin/google-chrome)
BUSCADOR_TUNNEL=0 BUSCADOR_PUBLISH=0 BUSCADOR_PORT=18791 BUSCADOR_LOCAL_PIN=prueba .venv/bin/python collector/buscador.py
curl -H 'X-Radar-Pin: prueba' 'http://127.0.0.1:18791/search?q=capybara'
RADAR_PIN=prueba RADAR_BOX_URL=http://127.0.0.1:18791 node scripts/dev_server.js 8766   # panel + /api en http://127.0.0.1:8766
RADAR_TEST_PIN=prueba .venv/bin/python scripts/screenshot_buscador.py http://127.0.0.1:8766/ capybara 30   # periodo 7|30|90
```

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
# el entorno debe tener GITHUB_TOKEN_TIKTOK_RADAR (publicar) y TELEGRAM_BOT_TOKEN_TIKTOK_RADAR (avisos)
RADAR_PUBLISH=1 RADAR_REMOTE=https://github.com/trendtiktokradar/tiktok-radar.git \
  nohup setsid /workspace/tiktok-radar/scripts/loop.sh >/dev/null 2>&1 &
# al arrancar, el bucle lanza también el vigilante rápido de avisos (scripts/fastwatch.sh ensure) y el Buscador
pkill -f /workspace/tiktok-radar/scripts/loop.sh       # parar el bucle (ruta completa: no toca otros bucles del box)
scripts/fastwatch.sh stop                               # parar también el vigilante rápido (si no, sigue avisando)
scripts/fastwatch.sh status                             # en marcha / último latido / últimos avisos
tail -f logs/radar.log logs/fastwatch.log               # ver qué hacen
```
Si el box se reinicia, el bucle y el vigilante se paran: basta con relanzar el bucle (comando de arriba), que arranca
el vigilante y el Buscador solo (mientras, la Action de respaldo mantiene los datos, pero sin avisos).
Para que el vigilante coja un cambio de código: `scripts/fastwatch.sh restart` (con el token de Telegram en el entorno).

### Arrancar / parar el Buscador en el box
```bash
cd /workspace/tiktok-radar
scripts/buscador.sh start      # nohup setsid: Chrome headless + cloudflared; publica box.json (necesita GITHUB_TOKEN_TIKTOK_RADAR)
scripts/buscador.sh status     # en marcha / health / último túnel
scripts/buscador.sh stop       # para servicio, túnel y Chrome
tail -f logs/buscador.log
```
- `scripts/loop.sh` llama a `scripts/buscador.sh ensure` en cada vuelta (5 min): si el servicio está caído o no
  responde, lo arranca. Así, tras reiniciar el box **basta con relanzar el bucle** (comando de arriba) y el Buscador
  vuelve solo en ≤ 5 min con una URL nueva que publica en `box.json`.
- Perfil de Chrome propio y sin login en `~/.tiktok-radar-chrome` (fuera del repo). cloudflared en `~/.local/bin/cloudflared`
  (descarga: `curl -L -o ~/.local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x ~/.local/bin/cloudflared`).
- Puerto local 18790 (los 8790/8791 los usa el propio box).

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

#!/usr/bin/env bash
# Servicio del Buscador en el box (collector/buscador.py + quick tunnel de Cloudflare). Sin IA.
#   scripts/buscador.sh start    arranca en segundo plano (nohup setsid) si no está ya en marcha
#   scripts/buscador.sh stop     lo para (también cloudflared y Chrome)
#   scripts/buscador.sh restart
#   scripts/buscador.sh status
#   scripts/buscador.sh ensure   lo arranca solo si está caído (lo llama scripts/loop.sh cada 5 min)
# Necesita GITHUB_TOKEN_TIKTOK_RADAR en el entorno para publicar la URL del túnel (box.json, rama feedback).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDF="$ROOT/state/buscador.pid"
PORT="${BUSCADOR_PORT:-18790}"
PY="$ROOT/.venv/bin/python"
mkdir -p "$ROOT/logs" "$ROOT/state"

running() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
healthy() { curl -s -m 8 "http://127.0.0.1:$PORT/health" | grep -q '"ok": true'; }

start() {
  if running; then echo "ya está en marcha (pid $(cat "$PIDF"))"; return 0; fi
  # el log no crece sin límite
  if [ -f "$ROOT/logs/buscador.log" ] && [ "$(wc -c < "$ROOT/logs/buscador.log")" -gt 5000000 ]; then
    tail -n 3000 "$ROOT/logs/buscador.log" > "$ROOT/logs/buscador.log.1" && mv "$ROOT/logs/buscador.log.1" "$ROOT/logs/buscador.log"
  fi
  [ -f "$ROOT/logs/cloudflared.log" ] && [ "$(wc -c < "$ROOT/logs/cloudflared.log")" -gt 5000000 ] && : > "$ROOT/logs/cloudflared.log"
  rm -f "$PIDF"
  cd "$ROOT" && BUSCADOR_PIDFILE="$PIDF" nohup setsid "$PY" "$ROOT/collector/buscador.py" >> "$ROOT/logs/buscador.log" 2>&1 < /dev/null &
  for _ in $(seq 1 20); do [ -s "$PIDF" ] && break; sleep 0.5; done   # el propio servicio escribe su pid
  echo "Buscador arrancado (pid $(cat "$PIDF" 2>/dev/null)). Log: logs/buscador.log"
}

stop() {
  if running; then
    PID="$(cat "$PIDF")"
    kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID"
    for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
    kill -KILL -- "-$PID" 2>/dev/null || true
    echo "Buscador parado"
  else
    echo "no estaba en marcha"
  fi
  # restos (p. ej. pid perdido): por si acaso
  pkill -f "$ROOT/collector/[b]uscador.py" 2>/dev/null || true
  rm -f "$PIDF"
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status)
    if running; then echo "en marcha (pid $(cat "$PIDF"))"; healthy && echo "health: OK" || echo "health: NO responde"; else echo "parado"; fi
    grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$ROOT/logs/buscador.log" 2>/dev/null | tail -1 | sed 's/^/último túnel: /'
    ;;
  ensure)
    if ! running; then echo "$(date '+%F %T') Buscador caído: lo arranco"; start
    elif ! healthy; then
      sleep 20
      if ! healthy; then echo "$(date '+%F %T') Buscador no responde: reinicio"; stop; sleep 1; start; fi
    fi
    ;;
  *) echo "uso: $0 start|stop|restart|status|ensure"; exit 1 ;;
esac

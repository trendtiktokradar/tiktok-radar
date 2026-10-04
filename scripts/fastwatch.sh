#!/usr/bin/env bash
# Vigilante rápido de avisos (collector/fastwatch.py) en el box: cada ~45 s mira DEX PAID y BONDING de las coins
# TikTok fuertes y avisa por Telegram al momento. Sin IA. Independiente de la pasada lenta (scripts/loop.sh).
#   scripts/fastwatch.sh start    arranca en segundo plano (nohup setsid) si no está ya en marcha
#   scripts/fastwatch.sh stop     lo para
#   scripts/fastwatch.sh restart
#   scripts/fastwatch.sh status
#   scripts/fastwatch.sh ensure   lo arranca si está caído o colgado (lo llama scripts/loop.sh en cada vuelta)
# Necesita TELEGRAM_BOT_TOKEN_TIKTOK_RADAR en el entorno (sin él solo detecta y no avisa).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDF="$ROOT/state/fastwatch.pid"
LOG="$ROOT/logs/fastwatch.log"
mkdir -p "$ROOT/logs" "$ROOT/state"

running() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
# colgado = el latido (state/fastwatch.json) lleva más de 5 min sin actualizarse
stale() { [ ! -f "$ROOT/state/fastwatch.json" ] || [ $(( $(date +%s) - $(stat -c %Y "$ROOT/state/fastwatch.json") )) -gt 300 ]; }

start() {
  if running; then echo "ya está en marcha (pid $(cat "$PIDF"))"; return 0; fi
  # el log no crece sin límite
  if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5000000 ]; then tail -n 3000 "$LOG" > "$LOG.1" && mv "$LOG.1" "$LOG"; fi
  rm -f "$PIDF"
  cd "$ROOT" && nohup setsid python3 "$ROOT/collector/fastwatch.py" >> "$LOG" 2>&1 < /dev/null &
  for _ in $(seq 1 20); do [ -s "$PIDF" ] && break; sleep 0.5; done   # el propio vigilante escribe su pid
  echo "$(date '+%F %T') vigilante rápido arrancado (pid $(cat "$PIDF" 2>/dev/null)). Log: logs/fastwatch.log"
}

stop() {
  if running; then
    PID="$(cat "$PIDF")"
    kill -TERM "$PID"
    for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
    kill -KILL "$PID" 2>/dev/null || true
    echo "vigilante rápido parado"
  else
    echo "no estaba en marcha"
  fi
  pkill -f "$ROOT/collector/[f]astwatch.py" 2>/dev/null || true
  rm -f "$PIDF"
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status)
    if running; then echo "en marcha (pid $(cat "$PIDF"))"; else echo "parado"; fi
    [ -f "$ROOT/state/fastwatch.json" ] && echo "último latido: $(date -d @"$(stat -c %Y "$ROOT/state/fastwatch.json")" '+%F %T')"
    grep "ciclo\|AVISO" "$LOG" 2>/dev/null | tail -3
    ;;
  ensure)
    if ! running; then echo "$(date '+%F %T') vigilante rápido caído: lo arranco"; start
    elif stale; then echo "$(date '+%F %T') vigilante rápido colgado (sin latido): reinicio"; stop; sleep 1; start; fi
    ;;
  *) echo "uso: $0 start|stop|restart|status|ensure"; exit 1 ;;
esac

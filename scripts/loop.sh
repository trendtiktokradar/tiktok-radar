#!/usr/bin/env bash
# Bucle 24/7 en el box: una pasada cada RADAR_INTERVAL segundos (300 = 5 min). Sin IA, sin tokens.
# Arrancar en segundo plano:
#   RADAR_PUBLISH=1 RADAR_REMOTE=git@github.com:USUARIO/tiktok-radar.git RADAR_SSH_KEY=~/.ssh/tiktok_radar_deploy \
#     nohup scripts/loop.sh >/dev/null 2>&1 &
# Parar:  pkill -f /workspace/tiktok-radar/scripts/loop.sh  (el Buscador y el vigilante rápido siguen: ver README)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/logs"
while true; do
  # vigilante rápido de avisos de Telegram (cada ~45 s, aparte): si está caído o colgado lo arranca (FASTWATCH=0 lo desactiva)
  [ "${FASTWATCH:-1}" = "1" ] && "$ROOT/scripts/fastwatch.sh" ensure >> "$ROOT/logs/radar.log" 2>&1
  { echo "=== $(date '+%F %T')"; "$ROOT/scripts/run_once.sh"; } >> "$ROOT/logs/radar.log" 2>&1
  # vigilante del Buscador: si el servicio está caído, lo vuelve a arrancar (BUSCADOR=0 lo desactiva)
  [ "${BUSCADOR:-1}" = "1" ] && "$ROOT/scripts/buscador.sh" ensure >> "$ROOT/logs/radar.log" 2>&1
  # el log no crece sin límite
  if [ "$(wc -c < "$ROOT/logs/radar.log")" -gt 5000000 ]; then tail -n 2000 "$ROOT/logs/radar.log" > "$ROOT/logs/radar.log.1" && mv "$ROOT/logs/radar.log.1" "$ROOT/logs/radar.log"; fi
  sleep "${RADAR_INTERVAL:-300}"
done

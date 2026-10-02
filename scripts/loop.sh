#!/usr/bin/env bash
# Bucle 24/7 en el box: una pasada cada RADAR_INTERVAL segundos (300 = 5 min). Sin IA, sin tokens.
# Arrancar en segundo plano:
#   RADAR_PUBLISH=1 RADAR_REMOTE=git@github.com:USUARIO/tiktok-radar.git RADAR_SSH_KEY=~/.ssh/tiktok_radar_deploy \
#     nohup scripts/loop.sh >/dev/null 2>&1 &
# Parar:  pkill -f tiktok-radar/scripts/loop.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/logs"
while true; do
  { echo "=== $(date '+%F %T')"; "$ROOT/scripts/run_once.sh"; } >> "$ROOT/logs/radar.log" 2>&1
  # el log no crece sin límite
  if [ "$(wc -c < "$ROOT/logs/radar.log")" -gt 5000000 ]; then tail -n 2000 "$ROOT/logs/radar.log" > "$ROOT/logs/radar.log.1" && mv "$ROOT/logs/radar.log.1" "$ROOT/logs/radar.log"; fi
  sleep "${RADAR_INTERVAL:-300}"
done

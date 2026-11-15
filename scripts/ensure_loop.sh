#!/usr/bin/env bash
# Idempotente: arranca scripts/loop.sh si no está en marcha (publica datos + vigilante + Buscador).
# Se llama desde ~/.profile y ~/.bashrc, igual que otros servicios del box, porque aquí no hay
# systemd como init ni cron: tras un reinicio, el bucle vuelve en cuanto abre un terminal o un agente.
# Hereda los tokens del entorno; nunca los escribe a disco.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK=/tmp/tiktok-radar-loop.lock
LOG="$ROOT/logs/ensure.log"
PATTERN="/workspace/tiktok-radar/scripts/loop.sh"
mkdir -p "$ROOT/logs"

if pgrep -f "$PATTERN" >/dev/null 2>&1; then
  exit 0
fi
exec 9>"$LOCK"
flock -n 9 || exit 0
if pgrep -f "$PATTERN" >/dev/null 2>&1; then
  exit 0
fi

export RADAR_PUBLISH=1
export RADAR_REMOTE="${RADAR_REMOTE:-https://github.com/trendtiktokradar/tiktok-radar.git}"
if [ -z "${GITHUB_TOKEN_TIKTOK_RADAR:-}" ] || [ -z "${TELEGRAM_BOT_TOKEN_TIKTOK_RADAR:-}" ]; then
  echo "$(date '+%F %T') aviso: arranco el bucle pero faltan tokens en el entorno (publicar/avisos)" >> "$LOG"
fi
cd "$ROOT"
nohup setsid "$ROOT/scripts/loop.sh" >/dev/null 2>&1 < /dev/null &
echo "$(date '+%F %T') arrancado loop.sh (pid $!)" >> "$LOG"
exit 0

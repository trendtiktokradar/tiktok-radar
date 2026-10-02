#!/usr/bin/env bash
# Una pasada completa: (1) recupera el estado más reciente de GitHub si existe, (2) ejecuta el collector,
# (3) publica data.json + state.json en la rama "data" si RADAR_PUBLISH=1.
# No usa IA. Pensado para ejecutarse cada 5 minutos (scripts/loop.sh o cron).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p logs state
exec 9>"$ROOT/.radar.lock"
flock -n 9 || { echo "$(date '+%F %T') otra pasada sigue en marcha, salto"; exit 0; }

if [ "${RADAR_PUBLISH:-0}" = "1" ]; then
  "$ROOT/scripts/publish.sh" pull || echo "aviso: no se pudo traer el estado remoto (sigo con el local)"
fi
python3 "$ROOT/collector/radar.py"
if [ "${RADAR_PUBLISH:-0}" = "1" ]; then
  "$ROOT/scripts/publish.sh" push
fi

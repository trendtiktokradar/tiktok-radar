#!/usr/bin/env bash
# Publica los datos en la rama "data" del repo de GitHub, SIN tocar la rama main
# (Vercel ignora la rama "data" gracias a web/vercel.json, así que no gasta despliegues).
#
#   publish.sh pull  -> si el state.json de GitHub es más nuevo que el local (p.ej. lo escribió la
#                        GitHub Action de respaldo mientras el box estaba caído), lo copia a state/
#   publish.sh push  -> sube web/data.json y state/state.json como UN único commit que se reemplaza
#                        en cada pasada (force-push SOLO de la rama data), para que el repo no crezca.
#
# Variables:
#   RADAR_REMOTE   URL git del repo (por defecto la de "origin"), p.ej. git@github.com:USUARIO/tiktok-radar.git
#   RADAR_SSH_KEY  ruta a una deploy key SSH (opcional)
#   GITHUB_TOKEN_TIKTOK_RADAR  token de GitHub (o el nombre que diga RADAR_TOKEN_VAR); se usa vía
#                  credential helper leyendo la variable, sin guardarlo en disco
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUB="$ROOT/.publish"
BRANCH="${RADAR_BRANCH:-data}"
REMOTE="${RADAR_REMOTE:-$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)}"
[ -n "$REMOTE" ] || { echo "Falta RADAR_REMOTE (o un remote 'origin')"; exit 1; }
if [ -n "${RADAR_SSH_KEY:-}" ]; then
  export GIT_SSH_COMMAND="ssh -i $RADAR_SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi
# Token por HTTPS: se lee de la variable de entorno en el momento (nunca se escribe en .git/config ni en logs).
TOKEN_VAR="${RADAR_TOKEN_VAR:-GITHUB_TOKEN_TIKTOK_RADAR}"
CRED=()
if [ -n "${!TOKEN_VAR:-}" ]; then
  CRED=(-c credential.helper= -c "credential.helper=!f() { echo username=x-access-token; echo password=\${$TOKEN_VAR}; }; f")
fi
if [ ! -d "$PUB/.git" ]; then
  git init -q "$PUB"
  git -C "$PUB" remote add origin "$REMOTE"
fi
git -C "$PUB" remote set-url origin "$REMOTE"
G() { git -C "$PUB" "${CRED[@]}" -c user.name="tiktok-radar-bot" -c user.email="tiktok-radar-bot@users.noreply.github.com" "$@"; }

case "${1:-push}" in
  pull)
    if G fetch -q --depth 1 origin "$BRANCH" 2>/dev/null; then
      G show FETCH_HEAD:state.json > "$PUB/remote_state.json" 2>/dev/null || exit 0
      python3 - "$PUB/remote_state.json" "$ROOT/state/state.json" <<'PY'
import json, sys, shutil
r, l = sys.argv[1], sys.argv[2]
try: rr = json.load(open(r)).get("last_run", 0)
except Exception: sys.exit(0)
try: ll = json.load(open(l)).get("last_run", 0)
except Exception: ll = 0
if rr > ll:
    shutil.copy(r, l); print(f"estado remoto más nuevo ({rr} > {ll}): copiado a state/")
PY
    fi
    ;;
  push)
    cp "$ROOT/web/data.json" "$PUB/data.json"
    cp "$ROOT/state/state.json" "$PUB/state.json"
    printf '# Rama de datos de TikTok Radar\nLa escribe el programa automáticamente. No editar a mano.\n' > "$PUB/README.md"
    G symbolic-ref HEAD "refs/heads/$BRANCH"
    G add -A
    if G rev-parse -q --verify HEAD >/dev/null; then
      G commit -q --amend --reset-author -m "data $(date -u +%FT%TZ)"
    else
      G commit -q -m "data $(date -u +%FT%TZ)"
    fi
    G push -q --force origin "HEAD:refs/heads/$BRANCH"
    echo "$(date '+%F %T') publicado en rama $BRANCH"
    ;;
esac

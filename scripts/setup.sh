#!/usr/bin/env bash
# Idempotente: instala o repara las dependencias del radar tras un reinicio del box.
# El collector y el vigilante rápido usan python3 del sistema (solo stdlib).
# El Buscador (y las capturas) usan .venv: playwright, aiohttp, pytrends + Chrome y ssh (túnel Pinggy).
# No escribe tokens a disco. No toca experiments/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
export DEBIAN_FRONTEND=noninteractive
PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

have() { command -v "$1" >/dev/null 2>&1; }
apt_ok() { have sudo && sudo -n true 2>/dev/null; }
apt_install() {
  apt_ok || { echo "setup: no puedo instalar con apt (falta sudo): $*"; return 1; }
  sudo -n apt-get update -qq
  sudo -n apt-get install -y -qq "$@"
}

echo "setup: comprobando dependencias del sistema…"
if ! have python3; then
  apt_install python3 || { echo "setup: falta python3"; exit 1; }
fi
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
  apt_install python3-venv python3-pip || { echo "setup: falta python3-venv"; exit 1; }
fi
if ! have ssh; then
  apt_install openssh-client || { echo "setup: falta openssh-client (túnel Pinggy)"; exit 1; }
fi
if ! have openssl; then
  apt_install openssl || { echo "setup: falta openssl (túnel Pinggy por TLS 443)"; exit 1; }
fi
if [ ! -x /usr/bin/google-chrome ] && ! have google-chrome && ! have chromium && ! have chromium-browser; then
  # el repo de Google ya está en esta máquina cuando Chrome se instaló una vez
  apt_install google-chrome-stable || apt_install chromium || {
    echo "setup: falta Chrome/Chromium (el Buscador lo necesita en modo headless)"
    exit 1
  }
fi

# cloudflared: ya no lo usa el Buscador (va por Pinggy), pero se deja listo por si hace falta
if [ ! -x "$HOME/.local/bin/cloudflared" ] && ! have cloudflared; then
  mkdir -p "$HOME/.local/bin"
  echo "setup: descargo cloudflared…"
  curl -fsSL -o "$HOME/.local/bin/cloudflared" \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x "$HOME/.local/bin/cloudflared"
fi

need_venv=0
if [ ! -x "$PY" ]; then
  need_venv=1
elif ! "$PY" -c "import aiohttp, playwright, pytrends" 2>/dev/null; then
  need_venv=1
fi
if [ "$need_venv" = "1" ]; then
  echo "setup: creo/reparo $VENV…"
  if [ -d "$VENV" ] && [ ! -x "$PY" ]; then
    rm -rf "$VENV"
  fi
  if [ ! -x "$PY" ]; then
    python3 -m venv "$VENV"
  fi
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$ROOT/requirements-dev.txt"
  # el Buscador lanza /usr/bin/google-chrome; no hace falta descargar Chromium de Playwright
  echo "setup: .venv listo ($("$PY" -c 'import aiohttp,playwright,pytrends; print("aiohttp+playwright+pytrends ok")'))"
else
  echo "setup: .venv ya está completo"
fi

# comprobación final
err=0
[ -x "$PY" ] || { echo "setup: falta $PY"; err=1; }
"$PY" -c "import aiohttp, playwright, pytrends" 2>/dev/null || { echo "setup: faltan paquetes en .venv"; err=1; }
[ -x /usr/bin/google-chrome ] || have google-chrome || have chromium || { echo "setup: falta Chrome"; err=1; }
have ssh || { echo "setup: falta ssh"; err=1; }
have openssl || { echo "setup: falta openssl"; err=1; }
if [ "$err" -ne 0 ]; then
  echo "setup: falló la comprobación final"
  exit 1
fi
echo "setup: todo listo"

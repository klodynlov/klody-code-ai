#!/usr/bin/env bash
# Active l'interface HTTP de VLC (une fois) pour que le serveur MCP VLC puisse
# le piloter : extraintf=http + http-host + http-port + http-password dans vlcrc.
#
# VLC réécrit vlcrc quand il quitte → le script REFUSE de tourner si VLC est
# lancé (sinon la config serait écrasée à la fermeture, panne silencieuse).
#
# Le mot de passe est généré, écrit dans vlcrc (mode 600) et dans le .env du
# projet (VLC_HTTP_PASSWORD). Il n'apparaît sur AUCUNE ligne de commande, donc
# pas dans `ps`.
#
# Usage:
#   ./scripts/setup-vlc-http.sh            # port 8092, mot de passe généré
#   VLC_HTTP_PORT=9010 ./scripts/setup-vlc-http.sh

set -euo pipefail

PORT="${VLC_HTTP_PORT:-8092}"
HOST="${VLC_HTTP_HOST:-127.0.0.1}"
VLCRC="$HOME/Library/Preferences/org.videolan.vlc/vlcrc"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

if pgrep -x VLC >/dev/null 2>&1; then
  echo "✗ VLC est lancé. Quitte-le d'abord (⌘Q) — il écraserait vlcrc en quittant." >&2
  exit 1
fi

if [[ ! -f "$VLCRC" ]]; then
  echo "✗ vlcrc introuvable ($VLCRC). Lance VLC une fois puis quitte-le." >&2
  exit 1
fi

BACKUP="$VLCRC.bak-$(date +%Y%m%d%H%M%S)"
cp -p "$VLCRC" "$BACKUP"
echo "• Sauvegarde : $BACKUP"

# Mot de passe : on réutilise celui du .env s'il existe déjà (relance idempotente).
PASSWORD="$(grep -E '^VLC_HTTP_PASSWORD=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
if [[ -z "$PASSWORD" ]]; then
  PASSWORD="$(openssl rand -hex 16)"
  echo "• Mot de passe généré (32 hex)"
else
  echo "• Mot de passe repris du .env"
fi

# Remplace la ligne si elle existe (commentée ou non), sinon l'ajoute.
set_vlcrc() {
  local key="$1" val="$2"
  if grep -qE "^#?${key}=" "$VLCRC"; then
    # LC_ALL=C : vlcrc n'est pas garanti UTF-8, sed BSD s'en étrangle sinon.
    LC_ALL=C sed -i '' -E "s|^#?${key}=.*|${key}=${val}|" "$VLCRC"
  else
    printf '%s=%s\n' "$key" "$val" >> "$VLCRC"
  fi
}

set_vlcrc extraintf http
set_vlcrc http-host "$HOST"
set_vlcrc http-port "$PORT"
set_vlcrc http-password "$PASSWORD"
chmod 600 "$VLCRC"

# .env du projet : le serveur MCP lit VLC_HTTP_* via load_dotenv().
touch "$ENV_FILE"
for pair in "VLC_HTTP_HOST=$HOST" "VLC_HTTP_PORT=$PORT" "VLC_HTTP_PASSWORD=$PASSWORD"; do
  key="${pair%%=*}"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    LC_ALL=C sed -i '' -E "s|^${key}=.*|${pair}|" "$ENV_FILE"
  else
    printf '%s\n' "$pair" >> "$ENV_FILE"
  fi
done
chmod 600 "$ENV_FILE"

echo "✓ vlcrc : extraintf=http, http-host=$HOST, http-port=$PORT, http-password=***"
echo "✓ .env  : VLC_HTTP_HOST/PORT/PASSWORD à jour"
echo
echo "Suite : relance VLC, puis vérifie —"
echo "  curl -s -u :\"\$(grep '^VLC_HTTP_PASSWORD=' $ENV_FILE | cut -d= -f2-)\" http://$HOST:$PORT/requests/status.json | head -c 200"

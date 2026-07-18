#!/bin/sh
# Watchdog de l'API Klody (:8000) — piloté par com.klody.api-watchdog (60 s).
#
# Raison d'être : le KeepAlive du LaunchAgent com.klody.api ne couvre que
# `Crashed` et `SuccessfulExit`. Un process *hung* — socket :8000 encore bindée,
# event loop qui n'accepte plus — ne crashe pas et ne sort pas, donc launchd ne
# le relance jamais. Symptôme côté UI : « Backend indisponible. Reconnexion
# automatique… » à l'infini, alors que le TCP répond (le noyau complète le
# handshake tout seul depuis le backlog).
#
# Règle « un seul owner par service » : ce script ne démarre JAMAIS l'API à la
# main, il passe par `launchctl kickstart` — com.klody.api reste seul owner.

set -u

HEALTH_URL="http://127.0.0.1:8000/health"
SERVICE="gui/$(id -u)/com.klody.api"
STATE_DIR="$HOME/Library/Caches/klody"
FAIL_FILE="$STATE_DIR/api-watchdog.fails"
LAST_KICK_FILE="$STATE_DIR/api-watchdog.last-kick"

# Nb d'échecs consécutifs avant relance. 2 → on ne tue pas l'API pour un unique
# hoquet, et on détecte quand même en ~2 min.
FAIL_THRESHOLD=2
# Timeout de la sonde. C'est LE paramètre qui distingue « hung » de « lent » :
# une API saine répond en quelques ms, une API coincée ne répond jamais.
PROBE_TIMEOUT=5
# Silence après une relance, le temps que l'API reboote (chargement MLX inclus).
# Sans ça : relance → encore KO au tick suivant → relance → boucle.
COOLDOWN=120

mkdir -p "$STATE_DIR"

log() {
    printf '%s api-watchdog: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

now=$(date +%s)

# --- Cooldown post-relance ---------------------------------------------------
if [ -f "$LAST_KICK_FILE" ]; then
    last_kick=$(cat "$LAST_KICK_FILE" 2>/dev/null || echo 0)
    case "$last_kick" in
        ''|*[!0-9]*) last_kick=0 ;;
    esac
    if [ "$((now - last_kick))" -lt "$COOLDOWN" ]; then
        exit 0
    fi
fi

# --- Sonde -------------------------------------------------------------------
# On ne regarde PAS le code HTTP : /health renvoie 503 quand le LLM est dégradé,
# mais un 503 prouve que l'app est vivante et répond. Relancer là-dessus ferait
# boucler le watchdog dès que MLX tousse. Seule l'ABSENCE de réponse HTTP
# (curl 28 = timeout, 7 = connexion refusée) signe la panne qu'on cible.
curl -s -o /dev/null --max-time "$PROBE_TIMEOUT" "$HEALTH_URL"
probe_rc=$?

if [ "$probe_rc" -eq 0 ]; then
    # Réponse HTTP obtenue, quel que soit le code → l'API sert. On oublie
    # l'historique d'échecs (ils n'étaient pas consécutifs).
    [ -f "$FAIL_FILE" ] && rm -f "$FAIL_FILE"
    exit 0
fi

# --- Comptage des échecs consécutifs -----------------------------------------
fails=0
[ -f "$FAIL_FILE" ] && fails=$(cat "$FAIL_FILE" 2>/dev/null || echo 0)
case "$fails" in
    ''|*[!0-9]*) fails=0 ;;
esac
fails=$((fails + 1))
printf '%s' "$fails" > "$FAIL_FILE"

case "$probe_rc" in
    28) reason="timeout ${PROBE_TIMEOUT}s (process hung : socket bindée, pas de réponse)" ;;
    7)  reason="connexion refusée (process absent)" ;;
    *)  reason="curl rc=$probe_rc" ;;
esac

if [ "$fails" -lt "$FAIL_THRESHOLD" ]; then
    log "sonde KO ($reason) — $fails/$FAIL_THRESHOLD, on attend le prochain tick"
    exit 0
fi

# --- Relance -----------------------------------------------------------------
log "sonde KO ($reason) — $fails/$FAIL_THRESHOLD atteint, kickstart de com.klody.api"
if launchctl kickstart -k "$SERVICE" 2>&1; then
    log "kickstart émis sur $SERVICE"
else
    log "ÉCHEC du kickstart sur $SERVICE (rc=$?)"
fi

printf '%s' "$now" > "$LAST_KICK_FILE"
rm -f "$FAIL_FILE"

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

# --- Capture avant de tuer ---------------------------------------------------
# Sans ça, chaque figeage emporte sa cause avec lui : le process est tué, et il
# ne reste qu'un log figé. La cause racine du figeage du 2026-07-19 est restée
# inconnue exactement pour cette raison. On capture donc AVANT le kickstart.
#
# Deux angles complémentaires :
#   - SIGUSR1 → l'API dumpe elle-même les piles PYTHON de tous ses threads
#     (faulthandler, armé dans api/server.py). C'est le seul angle qui dit quel
#     coroutine ou quel thread retient le serveur.
#   - `sample` → pile NATIVE, qui distingue « bloqué en syscall » de « en boucle ».
#     py-spy donnerait mieux, mais exige root sur macOS : hors de portée ici.
#
# Limite mesurée : un process arrêté par SIGSTOP ne TRAITE pas SIGUSR1 (le signal
# reste pendant jusqu'au SIGCONT), donc simuler un figeage par SIGSTOP ne produit
# aucun dump Python — seul `sample` répond dans ce cas. Sur un figeage RÉEL, où le
# process tourne mais dont la boucle est bloquée, faulthandler écrit bien : son
# handler s'exécute côté C et n'attend pas que l'interpréteur redevienne
# disponible. C'est le cas qui nous intéresse.
capture_diagnostic() {
    pid=$(lsof -nP -a -iTCP:8000 -sTCP:LISTEN -t 2>/dev/null | head -1)
    [ -z "$pid" ] && { log "pas de PID sur :8000, capture ignorée"; return; }

    stamp=$(date '+%Y%m%d-%H%M%S')
    native="$HOME/Library/Logs/klody-api-hang-$stamp.sample.txt"

    # Le dump Python atterrit dans klody-api-hang.log (fd gardé ouvert par l'API).
    kill -USR1 "$pid" 2>/dev/null && log "SIGUSR1 envoyé à $pid (dump des piles Python)"

    # `sample` occupe le process quelques secondes ; il laisse aussi le temps au
    # dump faulthandler d'être écrit avant le kill.
    if sample "$pid" 3 -file "$native" >/dev/null 2>&1; then
        log "pile native capturée → $native"
    else
        log "échec de sample sur $pid"
    fi

    {
        echo "=== état $stamp (pid $pid) ==="
        ps -o pid,ppid,stat,etime,%cpu,rss,command -p "$pid" 2>/dev/null
        echo "--- sockets ---"
        lsof -nP -a -p "$pid" -i 2>/dev/null
        echo "--- descripteurs ---"
        lsof -p "$pid" 2>/dev/null | wc -l
    } >> "$HOME/Library/Logs/klody-api-hang.log" 2>&1

    # Bornage : on garde les 5 dernières piles natives, pas plus (elles pèsent).
    ls -1t "$HOME/Library/Logs/"klody-api-hang-*.sample.txt 2>/dev/null \
        | tail -n +6 | while read -r old; do rm -f "$old"; done
}

capture_diagnostic

# --- Relance -----------------------------------------------------------------
log "sonde KO ($reason) — $fails/$FAIL_THRESHOLD atteint, kickstart de com.klody.api"
if launchctl kickstart -k "$SERVICE" 2>&1; then
    log "kickstart émis sur $SERVICE"
else
    log "ÉCHEC du kickstart sur $SERVICE (rc=$?)"
fi

printf '%s' "$now" > "$LAST_KICK_FILE"
rm -f "$FAIL_FILE"

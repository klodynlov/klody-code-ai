#!/bin/zsh
# Klody ← Siri bridge
# Usage: klody-siri.sh "votre question"
#   ou   echo "votre question" | klody-siri.sh
#
# Appelé par le Siri Shortcut "Demande à Klody".
# Retourne la réponse en texte brut, lue à voix haute par Siri.

set -euo pipefail

KLODY_API="http://localhost:8000/api/siri"
TIMEOUT=120

# Récupérer la question (arg ou stdin)
if [[ $# -gt 0 ]]; then
    QUERY="$*"
else
    QUERY=$(cat)
fi

if [[ -z "${QUERY// }" ]]; then
    echo "Klody : question vide."
    exit 0
fi

# Sérialiser en JSON pour éviter les injections
JSON_BODY=$(python3 -c "import json,sys; print(json.dumps({'query': sys.argv[1]}))" "$QUERY")

RESULT=$(curl -sf --max-time "$TIMEOUT" \
    -X POST "$KLODY_API" \
    -H "Content-Type: application/json" \
    -d "$JSON_BODY" 2>/dev/null) || {
    echo "Klody n'est pas disponible. Assurez-vous que le serveur Klody est démarré."
    exit 0
}

python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('response', 'Pas de réponse.'))
except Exception:
    print('Klody : erreur de lecture de la réponse.')
" <<< "$RESULT"

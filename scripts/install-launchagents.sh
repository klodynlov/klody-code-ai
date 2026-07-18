#!/bin/sh
# Installe (ou vérifie) les LaunchAgents de klody-code-ai versionnés dans
# launchagents/.
#
# Raison d'être : les scripts de service vivent dans le repo, mais ce qui les
# déclenche vivait uniquement dans ~/Library/LaunchAgents — hors versionnement.
# Sur une machine neuve le script était donc présent et l'agent absent : le
# service ne démarre jamais, sans la moindre erreur. Panne silencieuse.
#
# Usage :
#   install-launchagents.sh            installe / met à jour puis (re)charge
#   install-launchagents.sh --check    signale les écarts, n'écrit rien (CI)
#
# Idempotent : un agent dont le contenu rendu est déjà identique à l'installé
# n'est ni réécrit ni rechargé. Indispensable — un `bootstrap` inconditionnel
# redémarrerait l'API en pleine session de travail.

set -eu

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC_DIR="$REPO_ROOT/launchagents"
DEST_DIR="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

# Chemins figés à l'écriture des plists, réécrits pour la machine courante.
# L'ordre compte : le chemin du repo (le plus spécifique) avant le HOME.
ORIG_REPO="/Users/klodynlov/Projets/klody-code-ai"
ORIG_HOME="/Users/klodynlov"

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

[ -d "$SRC_DIR" ] || { echo "introuvable : $SRC_DIR" >&2; exit 1; }

render() {
    # plist versionné -> contenu adapté à cette machine, sur stdout
    sed -e "s|$ORIG_REPO|$REPO_ROOT|g" -e "s|$ORIG_HOME|$HOME|g" "$1"
}

drift=0
installed=0
skipped=0

for src in "$SRC_DIR"/*.plist; do
    [ -e "$src" ] || continue
    label=$(basename "$src" .plist)
    dest="$DEST_DIR/$label.plist"
    tmp=$(mktemp)
    render "$src" > "$tmp"

    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
        skipped=$((skipped + 1))
        rm -f "$tmp"
        continue
    fi

    if [ "$CHECK_ONLY" -eq 1 ]; then
        if [ -f "$dest" ]; then
            echo "ÉCART   $label (installé ≠ repo)"
            diff -u "$dest" "$tmp" | sed 's/^/    /' || true
        else
            echo "ABSENT  $label (versionné, pas installé)"
        fi
        drift=$((drift + 1))
        rm -f "$tmp"
        continue
    fi

    plutil -lint "$tmp" >/dev/null || { echo "plist invalide : $label" >&2; rm -f "$tmp"; exit 1; }
    mkdir -p "$DEST_DIR"
    mv "$tmp" "$dest"
    chmod 644 "$dest"

    # bootout puis bootstrap : recharge la définition. Le bootout échoue si le
    # service n'était pas chargé, ce qui est le cas nominal d'une 1re install.
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$dest"
    echo "installé  $label"
    installed=$((installed + 1))
done

if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ "$drift" -gt 0 ]; then
        echo "$drift agent(s) en écart, $skipped à jour." >&2
        exit 1
    fi
    echo "$skipped agent(s) à jour, aucun écart."
    exit 0
fi

echo "$installed installé(s), $skipped déjà à jour."

#!/usr/bin/env bash
# Canal iMessage (canaux/imessage.py) : coque KlodyMessages.app + agent launchd.
#
#   install-klody-messages.sh build [DEST]   construit + signe DEST/KlodyMessages.app (défaut build/)
#   install-klody-messages.sh install        ~/Applications/KlodyMessages.app + agent com.klody.imessage
#   install-klody-messages.sh uninstall      retire l'agent (l'app et ses droits restent)
#   SIGN_ID="-" … build                       signature ad-hoc (tests, sans trousseau)
#
# Pourquoi une coque : TCC donne l'Accès complet au disque (chat.db) et l'Automatisation
# → Messages au processus RESPONSABLE. La coque signée Developer ID porte seule ces droits ;
# le Python du venv n'en reçoit aucun. Exigence désignée stable (com.klody.imessage + Team
# ID) ⇒ les droits accordés survivent aux rebuilds (vérifié sur KlodyShield le 2026-10-19).
#
# Hors launchagents/ exprès : `install-launchagents.sh` installe tout ce dossier sans
# demander, or ce canal reste opt-in (il lit tes messages et répond en ton nom).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.klody.imessage"
APPS="$HOME/Applications"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/klody-imessage.log"
DOMAIN="gui/$(id -u)"
SIGN_ID="${SIGN_ID:-Developer ID Application: Claude volny (GM9K95VYY4)}"

# Sous-shell `( … )` + piège EXIT : le nettoyage reste LOCAL à build. Vécu au
# déploiement du 2026-11-21 : un `trap … RETURN` survit à la fonction et se
# redéclenche au retour de la suivante, où `work` n'existe plus → « work: unbound
# variable » sous `set -u`, après un ✅ — installeur en échec sur une install réussie.
build() (
    dest="${1:-$REPO/build}"
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    app="$work/KlodyMessages.app"
    mkdir -p "$app/Contents/MacOS"
    swiftc -O -swift-version 5 -o "$app/Contents/MacOS/KlodyMessages" "$REPO/canaux/imessage_app/main.swift"
    sed -e "s#__REPO__#$REPO#g" "$REPO/canaux/imessage_app/Info.plist" > "$app/Contents/Info.plist"
    plutil -lint "$app/Contents/Info.plist" >/dev/null
    printf 'APPL????' > "$app/Contents/PkgInfo"
    codesign --force --options runtime --timestamp=none \
        --entitlements "$REPO/canaux/imessage_app/KlodyMessages.entitlements" \
        --sign "$SIGN_ID" "$app"
    codesign --verify --strict "$app"
    # Remplacer un bundle signé : retirer l'ancien PUIS copier (écraser un Mach-O signé
    # en place invalide sa signature → SIGKILL 137 au lancement, sans message).
    mkdir -p "$dest"
    rm -rf "$dest/KlodyMessages.app"
    ditto "$app" "$dest/KlodyMessages.app"
    codesign --verify --strict "$dest/KlodyMessages.app"
    echo "$dest/KlodyMessages.app"
)

attendre_retrait() {
    # `launchctl bootout` rend la main AVANT le retrait effectif (vécu le 2026-09-30).
    for _ in $(seq 1 60); do
        launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || return 0
        sleep 0.5
    done
    echo "❌ $LABEL toujours chargé après 30 s" >&2
    return 1
}

install() {
    # La coque fige le chemin du dépôt : depuis un worktree, la prod servirait une branche.
    if [ "$(git -C "$REPO" rev-parse --git-dir)" != "$(git -C "$REPO" rev-parse --git-common-dir)" ]; then
        echo "❌ $REPO est un worktree : installer depuis le checkout principal." >&2
        exit 1
    fi
    if ! grep -Eq '^KLODY_IMSG_ALLOWED=.+' "$REPO/.env" 2>/dev/null; then
        echo "❌ KLODY_IMSG_ALLOWED absent de $REPO/.env (numéro ou Apple ID autorisé)." >&2
        exit 1
    fi
    build "$APPS" >/dev/null
    /usr/bin/python3 - "$REPO" "$APPS" "$LOG" "$PLIST.tmp" "$LABEL" <<'PY'
import plistlib, sys
repo, apps, log, out, label = sys.argv[1:]
p = {
    "Label": label,
    "ProgramArguments": [f"{apps}/KlodyMessages.app/Contents/MacOS/KlodyMessages"],
    "AssociatedBundleIdentifiers": [label],
    "WorkingDirectory": repo,
    "EnvironmentVariables": {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "HF_HUB_OFFLINE": "1",
    },
    "RunAtLoad": True,
    # Sortie 0 = « non configuré » (aucun expéditeur) : ne PAS relancer en boucle.
    "KeepAlive": {"SuccessfulExit": False, "Crashed": True},
    "ThrottleInterval": 30,
    "ProcessType": "Background",
    "StandardOutPath": log,
    "StandardErrorPath": log,
}
with open(out, "wb") as f:
    plistlib.dump(p, f)
PY
    plutil -lint "$PLIST.tmp" >/dev/null
    launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
    attendre_retrait
    mv "$PLIST.tmp" "$PLIST"
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "✅ $LABEL chargé — journal : $LOG"
    echo "   Accès complet au disque : ajouter $APPS/KlodyMessages.app (Réglages › Confidentialité)."
}

uninstall() {
    launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
    attendre_retrait
    rm -f "$PLIST"
    echo "✅ $LABEL retiré (l'app reste dans $APPS)"
}

case "${1:-}" in
    build) build "${2:-}" ;;
    install) install ;;
    uninstall) uninstall ;;
    *) echo "usage : $0 build [DEST] | install | uninstall" >&2; exit 2 ;;
esac

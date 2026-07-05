"""Pilotage de l'environnement macOS (Apple Silicon).

Klody s'appuie sur les briques natives de macOS via des binaires système :

- **AppleScript** (`osascript`)   → automatise n'importe quelle app scriptable.
- **Spotlight**   (`mdfind`)       → recherche indexée du système (lecture seule).
- **Raccourcis**  (`shortcuts`)    → exécute une action Raccourcis (elle-même
  passerelle vers HomeKit, Automator/Quick Actions, et toute automatisation).
- **Finder**      (`open -R`)      → révèle un fichier dans le Finder.

Principes de sûreté, alignés sur `tools/terminal.py` :

1. **Garde plateforme** — hors macOS, chaque outil rend un message clair au lieu
   de lever une exception (les tests tournent sur Linux en CI).
2. **Blocklist AppleScript** — les verbes destructeurs (suppression de fichiers,
   vidage de corbeille, extinction/redémarrage, `do shell script` chaîné à `rm`…)
   sont refusés AVANT exécution, comme la blocklist shell du terminal.
3. **Chemins sandboxés** — `reveal_in_finder` confine le chemin aux racines
   autorisées (`PROJECT_ROOT` + `ALLOWED_ROOTS`).
4. **Timeouts + sortie plafonnée** — aucun appel ne peut bloquer la boucle ReAct.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from config import (
    build_allowed_roots,
    match_allowed_root,
)

logger = logging.getLogger(__name__)

# Timeout par défaut d'un binaire macOS (s). Un raccourci HomeKit peut être lent.
_TIMEOUT_S = 30
# Sortie max renvoyée au LLM (caractères).
_MAX_OUTPUT = 20_000

# Sous-chaînes qui suffisent (insensible à la casse) à refuser un AppleScript.
# Même philosophie que terminal._BLOCKED_SUBSTRINGS : on bloque la destruction et
# l'exécution de shell arbitraire, on laisse passer le pilotage d'apps légitime.
_BLOCKED_APPLESCRIPT: tuple[str, ...] = (
    "do shell script",          # évasion vers un shell non contrôlé
    "delete",                   # Finder: delete (fichiers → corbeille)
    "empty trash",
    "erase",
    "shut down",
    "restart",
    "log out",
    "quit application \"finder\"",
    "system events",            # frappe/clics synthétiques (contrôle total UI)
)


class MacControlError(Exception):
    """Erreur de pilotage macOS (binaire absent, script refusé…)."""


def is_macos() -> bool:
    return sys.platform == "darwin"


def _guard_platform(tool: str) -> str | None:
    if not is_macos():
        return (
            f"⚠️ '{tool}' n'est disponible que sur macOS (Apple Silicon). "
            f"Plateforme courante : {sys.platform}."
        )
    return None


def _run(argv: list[str], timeout: int = _TIMEOUT_S) -> str:
    """Lance un binaire macOS et met en forme sa sortie pour le LLM."""
    # argv est une liste fixe (jamais shell=True) → pas d'injection shell.
    try:
        result = subprocess.run(  # nosec B603
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise MacControlError(f"Binaire introuvable : {argv[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise MacControlError(f"Timeout après {timeout}s : {argv[0]}") from e

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout.strip())
    if result.stderr:
        parts.append(f"[STDERR]\n{result.stderr.strip()}")
    if result.returncode != 0:
        parts.append(f"[Code de retour : {result.returncode}]")

    out = "\n".join(p for p in parts if p).strip() or "(aucune sortie)"
    if len(out) > _MAX_OUTPUT:
        out = out[:_MAX_OUTPUT] + f"\n… [tronqué — {len(out) - _MAX_OUTPUT} caractères]"
    return out


# --------------------------------------------------------------------------- #
# AppleScript                                                                  #
# --------------------------------------------------------------------------- #

def _check_applescript_safety(script: str) -> None:
    low = script.lower()
    for needle in _BLOCKED_APPLESCRIPT:
        if needle in low:
            raise MacControlError(
                f"AppleScript refusé (motif dangereux : '{needle}'). "
                f"Klody n'automatise pas les actions destructrices ou le contrôle "
                f"UI synthétique."
            )


def run_applescript(script: str, reason: str = "") -> str:
    """Exécute un AppleScript via `osascript -e` (après blocklist de sûreté).

    Args:
        script: le source AppleScript (ex. `tell application "Music" to play`).
        reason: justification affichée dans les logs (traçabilité).
    """
    guard = _guard_platform("run_applescript")
    if guard:
        return guard
    if not script or not script.strip():
        return "ERREUR : script AppleScript vide."

    try:
        _check_applescript_safety(script)
    except MacControlError as e:
        logger.warning("AppleScript bloqué : %s", e)
        return f"ERREUR SÉCURITÉ : {e}"

    logger.info("AppleScript (%s) : %.80s", reason or "sans raison", script)
    try:
        return _run(["osascript", "-e", script])
    except MacControlError as e:
        return f"ERREUR : {e}"


# --------------------------------------------------------------------------- #
# Spotlight (mdfind) — lecture seule                                            #
# --------------------------------------------------------------------------- #

def spotlight_search(query: str, only_in: str = "", limit: int = 20) -> str:
    """Recherche Spotlight indexée via `mdfind` (lecture seule).

    Args:
        query: requête Spotlight (ex. `kMDItemDisplayName == "*.pdf"` ou du texte).
        only_in: dossier où restreindre la recherche (optionnel).
        limit: nombre max de résultats renvoyés (défaut 20, plafonné à 200).
    """
    guard = _guard_platform("spotlight_search")
    if guard:
        return guard
    if not query or not query.strip():
        return "ERREUR : requête Spotlight vide."

    limit = max(1, min(int(limit or 20), 200))
    argv = ["mdfind"]
    if only_in.strip():
        argv += ["-onlyin", str(Path(only_in).expanduser())]
    argv.append(query)

    try:
        raw = _run(argv)
    except MacControlError as e:
        return f"ERREUR : {e}"
    if raw == "(aucune sortie)":
        return f"Aucun résultat Spotlight pour : {query!r}"

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    shown = lines[:limit]
    header = f"{len(lines)} résultat(s) Spotlight (affichés : {len(shown)}) :"
    body = "\n".join(f"  {ln}" for ln in shown)
    return f"{header}\n{body}"


# --------------------------------------------------------------------------- #
# Raccourcis (Shortcuts) — HomeKit / Automator / automatisations               #
# --------------------------------------------------------------------------- #

def list_shortcuts() -> str:
    """Liste les Raccourcis disponibles (`shortcuts list`), lecture seule."""
    guard = _guard_platform("list_shortcuts")
    if guard:
        return guard
    try:
        raw = _run(["shortcuts", "list"])
    except MacControlError as e:
        return f"ERREUR : {e}"
    if raw == "(aucune sortie)":
        return "Aucun raccourci trouvé."
    names = [ln for ln in raw.splitlines() if ln.strip()]
    return f"{len(names)} raccourci(s) :\n" + "\n".join(f"  • {n}" for n in names)


def run_shortcut(name: str, input_text: str = "") -> str:
    """Exécute un Raccourci Apple par son nom (`shortcuts run`).

    C'est la passerelle universelle : un Raccourci peut piloter HomeKit (scènes,
    accessoires), enchaîner une action Automator/Quick Action, ou lancer toute
    automatisation que l'utilisateur a créée.

    Args:
        name: nom exact du raccourci (voir `list_shortcuts`).
        input_text: entrée texte passée au raccourci (optionnel).
    """
    guard = _guard_platform("run_shortcut")
    if guard:
        return guard
    if not name or not name.strip():
        return "ERREUR : nom de raccourci vide."

    argv = ["shortcuts", "run", name]
    stdin_data = None
    if input_text.strip():
        # `shortcuts run` lit l'entrée depuis stdin quand on ne donne pas de -i fichier.
        argv += ["-i", "-"]
        stdin_data = input_text

    logger.info("Raccourci : %s", name)
    # argv est une liste fixe (jamais shell=True) → pas d'injection shell.
    try:
        result = subprocess.run(  # nosec B603
            argv,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return "ERREUR : binaire 'shortcuts' introuvable (macOS 12+ requis)."
    except subprocess.TimeoutExpired:
        return f"ERREUR : Timeout après {_TIMEOUT_S}s (raccourci '{name}')."

    if result.returncode != 0:
        err = (result.stderr or "").strip() or "(pas de détail)"
        return f"ERREUR raccourci '{name}' (code {result.returncode}) : {err}"
    out = (result.stdout or "").strip()
    return f"✅ Raccourci '{name}' exécuté." + (f"\nSortie :\n{out}" if out else "")


# --------------------------------------------------------------------------- #
# Finder                                                                        #
# --------------------------------------------------------------------------- #

def reveal_in_finder(path: str) -> str:
    """Révèle un fichier/dossier dans le Finder (`open -R`), chemin sandboxé."""
    guard = _guard_platform("reveal_in_finder")
    if guard:
        return guard
    if not path or not path.strip():
        return "ERREUR : chemin vide."

    p = Path(path).expanduser()
    resolved = p.resolve()
    roots = build_allowed_roots(Path.cwd())
    if match_allowed_root(resolved, roots) is None:
        return (
            f"ERREUR SÉCURITÉ : chemin hors des racines autorisées : {path} "
            f"→ {resolved}"
        )
    if not resolved.exists():
        return f"ERREUR : chemin introuvable : {path}"

    try:
        _run(["open", "-R", str(resolved)])
    except MacControlError as e:
        return f"ERREUR : {e}"
    return f"✅ Révélé dans le Finder : {resolved}"

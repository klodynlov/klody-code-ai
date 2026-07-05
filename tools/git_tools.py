"""Introspection Git en LECTURE SEULE — outil runtime (Roadmap v2 #10).

Permet à Klody d'inspecter l'état d'un dépôt Git (statut, historique, diff, blame…)
sans jamais le MUTER : aucune opération `commit`/`add`/`push`/`pull`/`checkout`/
`reset`/`merge`/`rebase`/`clean`/`stash`/`config --set`. Contrairement à
`execute_command` (confirmation TTY), cet outil est sûr et autonome car strictement
lecture seule. Même patron que docker_control / kubectl_control :

- `subprocess.run([...], shell=False)` — jamais de shell, jamais d'f-string.
- Sous-commandes Git **hardcodées** par action (jamais l'entrée utilisateur).
- Dépôt confiné aux racines autorisées (`match_allowed_root`).
- `ref` et `file` validés par charset strict, ne peuvent pas commencer par `-`
  (aucun flag injectable) ; `file` est une entrée repo-relative (pas de `..`).
- `git` absent / dossier non-repo → message clair ; sortie plafonnée ; timeout.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from config import PROJECT_ROOT, build_allowed_roots, match_allowed_root

logger = logging.getLogger(__name__)

_TIMEOUT_S = 15
_MAX_OUTPUT = 30_000
_DEFAULT_COUNT = 20
_MAX_COUNT = 200

# Racines autorisées (PROJECT_ROOT + ALLOWED_ROOTS). Overridable en test.
_GIT_ROOTS: list[Path] = build_allowed_roots(PROJECT_ROOT, None)

# Ref Git : commit/branche/tag/plage. Autorise les métacaractères de révision Git
# (~ ^ @ { } . /) mais AUCUN métacaractère shell (espace, ; | & $ ` …) ni '-' initial.
_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_./~^@{}-]{0,200}$")
# Fichier repo-relatif : pas de '/' initial, pas de '..', pas de métacaractère.
_FILE_RE = re.compile(r"^[a-zA-Z0-9._][a-zA-Z0-9._/\- ]{0,255}$")

_READ_ACTIONS = frozenset({
    "status", "log", "diff", "show", "blame", "branch", "tag", "remote", "shortlog",
})
_NEEDS_FILE = frozenset({"blame"})


class GitToolError(Exception):
    """Entrée invalide."""


def _resolve_repo(path: str) -> Path:
    """Résout + confine le dossier du dépôt. Lève GitToolError sinon."""
    raw = (path or "").strip()
    if not raw:
        return PROJECT_ROOT.resolve()
    if raw.lower().startswith("file:") or "\x00" in raw:
        raise GitToolError("Chemin de dépôt invalide.")
    p = Path(raw).expanduser()
    resolved = p.resolve() if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    if match_allowed_root(resolved, _GIT_ROOTS) is None:
        raise GitToolError(f"Dépôt hors des racines autorisées : '{path}'")
    if not resolved.is_dir():
        raise GitToolError(f"Dossier introuvable : '{path}'")
    return resolved


def _argv(action: str, ref: str, file: str, count: int) -> list[str]:
    """argv Git figé pour une action lecture seule (entrées déjà validées)."""
    ref_arg = [ref] if ref else []
    file_arg = ["--", file] if file else []
    if action == "status":
        return ["status", "--short", "--branch"]
    if action == "log":
        return ["log", "--oneline", "--decorate", "--no-color", "-n", str(count),
                *ref_arg, *file_arg]
    if action == "diff":
        return ["diff", "--no-color", *ref_arg, *file_arg]
    if action == "show":
        return ["show", "--stat", "--no-color", ref or "HEAD"]
    if action == "blame":
        return ["blame", "--", file] if not ref else ["blame", ref, "--", file]
    if action == "branch":
        return ["branch", "-a", "-vv", "--no-color"]
    if action == "tag":
        return ["tag", "--list"]
    if action == "remote":
        return ["remote", "-v"]
    if action == "shortlog":
        return ["shortlog", "-sne", "--no-merges", ref or "HEAD"]
    raise GitToolError(f"action inconnue : '{action}'")  # pragma: no cover


def git_control(action: str, path: str = "", ref: str = "", file: str = "",
                max_count: int = _DEFAULT_COUNT) -> dict:
    """Exécute une commande Git LECTURE SEULE. Retourne un dict structuré."""
    action = (action or "").strip().lower()
    if action not in _READ_ACTIONS:
        return {"ok": False, "error": (
            f"Action '{action}' non supportée. Actions lecture seule : "
            f"{', '.join(sorted(_READ_ACTIONS))}."
        )}

    ref = (ref or "").strip()
    file = (file or "").strip()

    if ref and not _REF_RE.match(ref):
        return {"ok": False, "error": "Ref invalide (commit/branche/tag ; pas de métacaractère ni '-' initial)."}
    if file and (".." in file or not _FILE_RE.match(file)):
        return {"ok": False, "error": "Chemin de fichier invalide (repo-relatif, sans '..')."}
    if action in _NEEDS_FILE and not file:
        return {"ok": False, "error": f"L'action '{action}' requiert un 'file'."}

    try:
        count = max(1, min(int(max_count), _MAX_COUNT))
    except (TypeError, ValueError):
        count = _DEFAULT_COUNT

    try:
        repo = _resolve_repo(path)
    except GitToolError as exc:
        return {"ok": False, "error": f"ERREUR SÉCURITÉ: {exc}"}

    if shutil.which("git") is None:
        return {"ok": False, "error": "git introuvable (binaire absent du PATH)."}

    argv = ["git", "-C", str(repo), *_argv(action, ref, file, count)]
    try:
        # argv figé + shell=False + entrées validées → pas d'injection de commande.
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_TIMEOUT_S, shell=False, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Commande git expirée (>{_TIMEOUT_S}s)."}
    except OSError as exc:
        return {"ok": False, "error": f"Échec d'exécution git : {exc}"}

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if "not a git repository" in err.lower():
            err = "ce dossier n'est pas un dépôt Git."
        return {"ok": False, "error": f"git a échoué : {err[:_MAX_OUTPUT]}"}

    out = (proc.stdout or "").strip()
    truncated = len(out) > _MAX_OUTPUT
    if truncated:
        out = out[:_MAX_OUTPUT] + "\n[…sortie tronquée…]"
    return {"ok": True, "action": action, "output": out, "truncated": truncated}


def format_git_result(res: dict) -> str:
    """Rend le résultat de git_control lisible pour le LLM."""
    if not res.get("ok"):
        return res.get("error", "Erreur git inconnue.")
    body = res.get("output") or "(aucune sortie)"
    return f"$ git {res['action']}\n{body}"

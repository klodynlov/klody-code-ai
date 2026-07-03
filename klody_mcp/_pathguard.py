"""Garde-fou chemins pour les serveurs MCP (ASI02 — Tool Misuse).

Les arguments d'outils MCP sont contrôlés par le modèle LLM (potentiellement
empoisonné via du contenu externe). Un arg chemin non validé = lecture/écriture
arbitraire : `../../../etc/passwd`, `~/.ssh/id_ed25519`, écraser un fichier
système. Ce module confine tout chemin d'outil sous des racines autorisées.

Volontairement SANS import de `config` : les serveurs MCP préservent l'isolation
de domaine (ils lisent le même .env mais n'importent pas l'agent). Racines via
env `KLODY_MCP_AUDIO_ROOTS` (séparateur `os.pathsep`), défauts sains couvrant les
emplacements audio légitimes.

Sécurité symlink : on `resolve()` (suit les liens jusqu'à la cible réelle) PUIS
on teste l'appartenance — un symlink placé dans une racine autorisée mais visant
l'extérieur est donc rejeté.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_HOME = Path.home()

# Défauts : emplacements audio/projet légitimes. /tmp inclus (REAPER y rend les
# stems, KlodyMusic y transcrit). PAS de $HOME nu — sinon ~/.ssh redevient lisible.
_DEFAULT_ROOTS = [
    _HOME / "local-suno",
    _HOME / "Music",
    _HOME / "Documents",
    _HOME / "Movies",
    _HOME / "Projets",
    Path(tempfile.gettempdir()),
]


def _load_roots() -> list[Path]:
    raw = os.getenv("KLODY_MCP_AUDIO_ROOTS", "")
    roots: list[Path] = []
    src = [Path(p) for p in raw.split(os.pathsep) if p.strip()] if raw else _DEFAULT_ROOTS
    for r in src:
        try:
            rr = r.expanduser().resolve()
        except OSError:
            continue
        if rr not in roots:
            roots.append(rr)
    return roots


# Calculées une fois au chargement (cohérent avec tools/audio._AUDIO_ROOTS).
AUDIO_ROOTS: list[Path] = _load_roots()


class PathGuardViolation(PermissionError):
    """Chemin hors des racines autorisées (traversal bloqué)."""


def _match_root(resolved: Path, roots: list[Path]) -> Path | None:
    for root in roots:
        try:
            resolved.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def safe_path(path: str | os.PathLike, *, must_exist: bool = True,
              for_write: bool = False, roots: list[Path] | None = None) -> Path:
    """Résout `path` et vérifie qu'il tombe sous une racine autorisée.

    Renvoie le Path absolu résolu. Lève PathGuardViolation hors sandbox,
    FileNotFoundError si must_exist et absent. Un chemin d'écriture voit son
    PARENT résolu (le fichier peut ne pas exister encore) ; la traversal reste
    bloquée car le parent doit être sous une racine.
    """
    roots = roots or AUDIO_ROOTS
    p = Path(path).expanduser()
    if for_write and not p.exists():
        # Fichier à créer : on valide le dossier parent (résolu), puis on
        # recompose — empêche `/etc/evil` tout en autorisant un nouveau nom.
        parent = p.parent.resolve()
        if _match_root(parent, roots) is None:
            raise PathGuardViolation(f"Écriture hors des racines autorisées : {path}")
        return parent / p.name
    resolved = p.resolve()
    if _match_root(resolved, roots) is None:
        raise PathGuardViolation(f"Chemin hors des racines autorisées : {path}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Fichier non trouvé : {path}")
    return resolved

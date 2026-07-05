"""Automatisation de fichiers : renommage, organisation, sauvegarde, synchro.

Tâches domestiques répétitives que Klody prend en charge, toutes **confinées au
sandbox multi-racines** (`PROJECT_ROOT` + `ALLOWED_ROOTS`) : impossible de toucher
un dossier hors des racines autorisées.

Discipline de sûreté :
- Les opérations **destructrices ou de masse** (`batch_rename`, `organize_directory`,
  `sync_directories`) sont en **`dry_run=True` par défaut** : elles montrent d'abord
  le plan, l'utilisateur relance avec `dry_run=false` pour appliquer.
- Les fichiers sensibles (`.env`, clés, certificats) sont **exclus partout** —
  jamais renommés, déplacés, copiés ou sauvegardés.
- Nombre d'entrées traitées plafonné (anti-emballement).
"""
from __future__ import annotations

import contextlib
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from config import build_allowed_roots, match_allowed_root

from tools.file_manager import BLOCKED_EXTENSIONS, BLOCKED_FILENAMES

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 1000            # fichiers traités par opération
_MAX_BACKUP_BYTES = 500 * 1024 * 1024  # 500 Mo pour une archive de sauvegarde


class AutomationError(Exception):
    """Opération d'automatisation refusée (hors sandbox, cible sensible…)."""


def _resolve_dir(path: str, must_exist: bool = True) -> Path:
    """Résout un dossier et le confine aux racines autorisées."""
    if not path or not path.strip():
        raise AutomationError("Chemin vide non autorisé.")
    p = Path(path).expanduser()
    resolved = p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()
    roots = build_allowed_roots(Path.cwd())
    if match_allowed_root(resolved, roots) is None:
        raise AutomationError(f"Chemin hors des racines autorisées : {path} → {resolved}")
    if must_exist and not resolved.exists():
        raise AutomationError(f"Dossier introuvable : {path}")
    if must_exist and not resolved.is_dir():
        raise AutomationError(f"'{path}' n'est pas un dossier.")
    return resolved


def _is_sensitive(p: Path) -> bool:
    return p.suffix.lower() in BLOCKED_EXTENSIONS or p.name in BLOCKED_FILENAMES


def _tag(dry_run: bool) -> str:
    return "🔍 SIMULATION (dry_run)" if dry_run else "✅ APPLIQUÉ"


# --------------------------------------------------------------------------- #
# Renommage par lot                                                            #
# --------------------------------------------------------------------------- #

def batch_rename(
    directory: str,
    pattern: str,
    replacement: str,
    use_regex: bool = False,
    recursive: bool = False,
    dry_run: bool = True,
) -> str:
    """Renomme en lot les fichiers d'un dossier (motif → remplacement).

    Args:
        directory: dossier cible (dans les racines autorisées).
        pattern: sous-chaîne (ou regex si use_regex) à trouver dans le NOM du fichier.
        replacement: texte de remplacement.
        use_regex: interpréter `pattern` comme une expression régulière.
        recursive: descendre dans les sous-dossiers.
        dry_run: si True (défaut), montre le plan sans renommer.
    """
    try:
        root = _resolve_dir(directory)
    except AutomationError as e:
        return f"ERREUR SÉCURITÉ : {e}"
    if not pattern:
        return "ERREUR : motif de recherche vide."

    try:
        rx = re.compile(pattern) if use_regex else None
    except re.error as e:
        return f"ERREUR : regex invalide — {e}"

    files = sorted(p for p in (root.rglob("*") if recursive else root.iterdir()) if p.is_file())
    plan: list[tuple[Path, Path]] = []
    skipped_sensitive = 0
    for f in files:
        if len(plan) >= _MAX_ENTRIES:
            break
        if _is_sensitive(f):
            skipped_sensitive += 1
            continue
        new_name = rx.sub(replacement, f.name) if rx else f.name.replace(pattern, replacement)
        if new_name != f.name and new_name.strip():
            # On garde le nom dans le même dossier (pas de traversée via /).
            if "/" in new_name or "\\" in new_name:
                continue
            plan.append((f, f.with_name(new_name)))

    if not plan:
        extra = f" ({skipped_sensitive} fichier(s) sensible(s) ignoré(s))" if skipped_sensitive else ""
        return f"Aucun fichier à renommer pour le motif {pattern!r}.{extra}"

    lines = [f"{_tag(dry_run)} — {len(plan)} renommage(s) :"]
    applied = 0
    conflicts = 0
    for src, dst in plan:
        if dst.exists():
            lines.append(f"  ⚠️  {src.name} → {dst.name} (existe déjà, ignoré)")
            conflicts += 1
            continue
        lines.append(f"  {src.name} → {dst.name}")
        if not dry_run:
            src.rename(dst)
            applied += 1

    if skipped_sensitive:
        lines.append(f"  ({skipped_sensitive} fichier(s) sensible(s) ignoré(s))")
    if dry_run:
        lines.append("→ Relance avec dry_run=false pour appliquer.")
    else:
        logger.info("batch_rename : %d renommé(s) dans %s", applied, root)
        lines.append(f"→ {applied} renommé(s), {conflicts} conflit(s).")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Organisation par type / date                                                 #
# --------------------------------------------------------------------------- #

# Regroupement des extensions en catégories lisibles.
_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Images": (".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".svg", ".tiff", ".bmp"),
    "Videos": (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"),
    "Audio": (".mp3", ".wav", ".aiff", ".flac", ".m4a", ".ogg", ".aac"),
    "Documents": (".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".pages", ".odt"),
    "Tableurs": (".xls", ".xlsx", ".csv", ".numbers", ".ods"),
    "Archives": (".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".dmg"),
    "Code": (".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c", ".cpp", ".sh", ".html", ".css", ".json"),
}


def _category_for(suffix: str) -> str:
    s = suffix.lower()
    for cat, exts in _CATEGORIES.items():
        if s in exts:
            return cat
    return "Autres"


def organize_directory(
    directory: str,
    by: str = "type",
    dry_run: bool = True,
) -> str:
    """Range les fichiers d'un dossier dans des sous-dossiers.

    Args:
        directory: dossier à organiser.
        by: critère de rangement — "type" (catégorie d'extension) ou
            "date" (année-mois de dernière modification, ex. 2026-07).
        dry_run: si True (défaut), montre le plan sans déplacer.
    """
    try:
        root = _resolve_dir(directory)
    except AutomationError as e:
        return f"ERREUR SÉCURITÉ : {e}"
    if by not in ("type", "date"):
        return "ERREUR : 'by' doit valoir 'type' ou 'date'."

    files = sorted(p for p in root.iterdir() if p.is_file())
    plan: list[tuple[Path, str]] = []
    skipped_sensitive = 0
    for f in files:
        if len(plan) >= _MAX_ENTRIES:
            break
        if _is_sensitive(f):
            skipped_sensitive += 1
            continue
        if by == "type":
            bucket = _category_for(f.suffix)
        else:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
            bucket = mtime.strftime("%Y-%m")
        plan.append((f, bucket))

    if not plan:
        return f"Aucun fichier à organiser dans {directory}."

    # Résumé par sous-dossier.
    from collections import Counter
    counts = Counter(bucket for _, bucket in plan)
    lines = [f"{_tag(dry_run)} — organisation de {len(plan)} fichier(s) par {by} :"]
    for bucket, n in sorted(counts.items()):
        lines.append(f"  📁 {bucket}/  ({n} fichier(s))")

    if not dry_run:
        moved = 0
        for src, bucket in plan:
            dest_dir = root / bucket
            dest_dir.mkdir(exist_ok=True)
            dest = dest_dir / src.name
            if dest.exists():
                continue
            src.rename(dest)
            moved += 1
        logger.info("organize_directory : %d déplacé(s) dans %s", moved, root)
        lines.append(f"→ {moved} fichier(s) déplacé(s).")
    else:
        lines.append("→ Relance avec dry_run=false pour appliquer.")
    if skipped_sensitive:
        lines.append(f"  ({skipped_sensitive} fichier(s) sensible(s) ignoré(s))")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Sauvegarde (archive .tar.gz horodatée)                                        #
# --------------------------------------------------------------------------- #

def backup_directory(source: str, destination: str = "") -> str:
    """Crée une archive `.tar.gz` horodatée d'un dossier.

    Args:
        source: dossier à sauvegarder.
        destination: dossier où écrire l'archive (défaut : parent de la source).
            L'archive s'appelle `<nom>-backup-<horodatage>.tar.gz`.
    """
    try:
        src = _resolve_dir(source)
    except AutomationError as e:
        return f"ERREUR SÉCURITÉ : {e}"

    try:
        dest_dir = _resolve_dir(destination) if destination.strip() else src.parent
    except AutomationError as e:
        return f"ERREUR SÉCURITÉ : {e}"

    # Taille totale (garde-fou).
    total = 0
    for p in src.rglob("*"):
        if p.is_file() and not p.is_symlink():
            with contextlib.suppress(OSError):
                total += p.stat().st_size
    if total > _MAX_BACKUP_BYTES:
        return (
            f"ERREUR : dossier trop volumineux à archiver "
            f"({total // (1024 * 1024)} Mo > {_MAX_BACKUP_BYTES // (1024 * 1024)} Mo)."
        )

    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    archive_base = dest_dir / f"{src.name}-backup-{stamp}"
    try:
        archive_path = shutil.make_archive(
            str(archive_base), "gztar", root_dir=str(src.parent), base_dir=src.name,
        )
    except Exception as e:
        logger.error("backup_directory échec %s : %s", src, e)
        return f"ERREUR : sauvegarde impossible — {e}"

    size = Path(archive_path).stat().st_size
    logger.info("backup_directory : %s (%d o)", archive_path, size)
    return f"✅ Sauvegarde créée : {archive_path} ({size:,} o)"


# --------------------------------------------------------------------------- #
# Synchronisation (miroir source → destination)                                #
# --------------------------------------------------------------------------- #

def sync_directories(
    source: str,
    destination: str,
    delete: bool = False,
    dry_run: bool = True,
) -> str:
    """Synchronise un dossier source vers une destination (copie incrémentale).

    Copie les fichiers nouveaux ou modifiés (mtime/taille) de la source vers la
    destination. Avec `delete=True`, supprime aussi de la destination ce qui
    n'existe plus dans la source (vrai miroir).

    Args:
        source: dossier source (référence).
        destination: dossier destination (mis à jour).
        delete: supprimer de la destination les fichiers absents de la source.
        dry_run: si True (défaut), montre le plan sans rien écrire.
    """
    try:
        src = _resolve_dir(source)
        dst = _resolve_dir(destination, must_exist=False)
    except AutomationError as e:
        return f"ERREUR SÉCURITÉ : {e}"

    to_copy: list[Path] = []
    for p in sorted(src.rglob("*")):
        if len(to_copy) >= _MAX_ENTRIES:
            break
        if not p.is_file() or p.is_symlink() or _is_sensitive(p):
            continue
        rel = p.relative_to(src)
        target = dst / rel
        if not target.exists():
            to_copy.append(rel)
        else:
            ss, ts = p.stat(), target.stat()
            if int(ss.st_mtime) != int(ts.st_mtime) or ss.st_size != ts.st_size:
                to_copy.append(rel)

    to_delete: list[Path] = []
    if delete and dst.exists():
        src_rel = {p.relative_to(src) for p in src.rglob("*") if p.is_file()}
        for p in dst.rglob("*"):
            if p.is_file() and p.relative_to(dst) not in src_rel:
                to_delete.append(p.relative_to(dst))

    lines = [f"{_tag(dry_run)} — synchro {src.name} → {dst.name} :"]
    lines.append(f"  {len(to_copy)} à copier, {len(to_delete)} à supprimer.")
    for rel in to_copy[:40]:
        lines.append(f"  + {rel}")
    if len(to_copy) > 40:
        lines.append(f"  … (+{len(to_copy) - 40} autres)")
    for rel in to_delete[:40]:
        lines.append(f"  - {rel}")

    if dry_run:
        lines.append("→ Relance avec dry_run=false pour appliquer.")
        return "\n".join(lines)

    copied = 0
    for rel in to_copy:
        s = src / rel
        t = dst / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, t)
        copied += 1
    deleted = 0
    for rel in to_delete:
        (dst / rel).unlink(missing_ok=True)
        deleted += 1
    logger.info("sync_directories : %d copié(s), %d supprimé(s) (%s→%s)", copied, deleted, src, dst)
    lines.append(f"→ {copied} copié(s), {deleted} supprimé(s).")
    return "\n".join(lines)

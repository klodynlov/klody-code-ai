import difflib
import logging
from collections.abc import Iterator
from pathlib import Path

from agent.dbc import ensure
from config import (
    MAX_FILE_SIZE,
    PROJECT_ROOT,
    build_allowed_roots,
    match_allowed_root,
)

logger = logging.getLogger(__name__)

BLOCKED_EXTENSIONS: frozenset[str] = frozenset({
    ".env", ".key", ".pem", ".p12", ".pfx", ".cer", ".crt", ".ppk", ".p8",
})
BLOCKED_FILENAMES: frozenset[str] = frozenset({
    ".env", ".env.local", ".env.production", ".env.staging",
})


class SandboxViolation(Exception):
    """Tentative d'accès hors du sandbox autorisé."""


class FileManager:
    def __init__(self, root: Path = PROJECT_ROOT, allowed_roots: list[Path] | None = None):
        self.root = root.resolve()
        # Racines où la lecture/écriture est permise. `self.root` (projet courant)
        # est toujours en tête ; les autres viennent d'ALLOWED_ROOTS ou de l'appelant.
        self.allowed_roots = build_allowed_roots(self.root, allowed_roots)

    # ------------------------------------------------------------------ #
    # Validation sandbox                                                   #
    # ------------------------------------------------------------------ #

    def _validate_path(self, path: str) -> Path:
        """
        Résout et valide un chemin (relatif au projet courant, ou absolu).
        Lève SandboxViolation s'il ne tombe sous aucune racine autorisée.
        """
        if not path or not path.strip():
            raise SandboxViolation("Chemin vide non autorisé")

        p = Path(path).expanduser()
        resolved = p.resolve() if p.is_absolute() else (self.root / p).resolve()

        if match_allowed_root(resolved, self.allowed_roots) is None:
            raise SandboxViolation(
                f"Chemin hors des racines autorisées: '{path}' → '{resolved}'"
            )

        # Symlink qui pointerait hors des racines autorisées
        if resolved.is_symlink():
            link_target = resolved.resolve()
            if match_allowed_root(link_target, self.allowed_roots) is None:
                raise SandboxViolation(
                    f"Symlink sortant des racines autorisées: '{path}' → '{link_target}'"
                )

        return resolved

    def _check_extension(self, path: Path) -> None:
        if path.suffix.lower() in BLOCKED_EXTENSIONS:
            raise SandboxViolation(f"Extension bloquée: '{path.suffix}'")
        if path.name in BLOCKED_FILENAMES:
            raise SandboxViolation(f"Fichier bloqué: '{path.name}'")

    # ------------------------------------------------------------------ #
    # Opérations                                                           #
    # ------------------------------------------------------------------ #

    def read_file(self, path: str) -> str:
        resolved = self._validate_path(path)
        self._check_extension(resolved)

        if not resolved.exists():
            raise FileNotFoundError(f"Fichier introuvable: {path}")
        if resolved.is_dir():
            raise IsADirectoryError(
                f"'{path}' est un répertoire, pas un fichier. "
                f"Utilise list_files('{path}') pour en voir le contenu."
            )

        size = resolved.stat().st_size
        if size > MAX_FILE_SIZE:
            raise ValueError(
                f"Fichier trop volumineux: {size:,} o (max: {MAX_FILE_SIZE:,} o)"
            )

        content = resolved.read_text(encoding="utf-8", errors="replace")
        logger.info("Lecture: %s (%d o)", path, size)
        return content

    def write_file(self, path: str, content: str) -> str:
        resolved = self._validate_path(path)
        self._check_extension(resolved)

        content_size = len(content.encode("utf-8"))
        if content_size > MAX_FILE_SIZE:
            raise ValueError(
                f"Contenu trop volumineux: {content_size:,} o (max: {MAX_FILE_SIZE:,} o)"
            )

        resolved.parent.mkdir(parents=True, exist_ok=True)

        old_content = ""
        existed = resolved.exists()
        if existed:
            old_content = resolved.read_text(encoding="utf-8", errors="replace")

        resolved.write_text(content, encoding="utf-8")
        # Postcondition : après une écriture réussie, le fichier existe bel et bien.
        ensure(resolved.exists(), f"le fichier doit exister après write_file: {path}")

        action = "modifié" if existed else "créé"
        logger.info("Écriture (%s): %s (%d caractères)", action, path, len(content))

        result = f"Fichier {action} avec succès: {path}"
        if existed and old_content != content:
            diff_lines = list(difflib.unified_diff(
                old_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=3,
            ))
            if diff_lines:
                diff_text = "".join(diff_lines[:80])
                if len(diff_lines) > 80:
                    diff_text += f"\n… ({len(diff_lines) - 80} lignes de diff supplémentaires)"
                result += f"\n\n{diff_text}"
        return result

    def list_files(self, path: str = ".", recursive: bool = False) -> str:
        resolved = self._validate_path(path)

        if not resolved.exists():
            raise FileNotFoundError(f"Répertoire introuvable: {path}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"'{path}' n'est pas un répertoire")

        # Noms masqués qu'ils soient fichiers ou dossiers
        _SKIP_NAMES = {
            ".git", ".claude", ".venv", "__pycache__", "node_modules",
            ".pytest_cache", ".mypy_cache", "target", "dist", ".next",
            "build", ".cache", ".env", ".env.local", ".env.production",
            "_preview",
        }
        MAX_ENTRIES = 150

        def _iter(base: Path, recurse: bool) -> Iterator[Path]:
            for entry in sorted(base.iterdir()):
                if entry.name in _SKIP_NAMES:
                    continue
                yield entry
                if recurse and entry.is_dir():
                    yield from _iter(entry, recurse)

        lines: list[str] = []
        truncated = 0
        for entry in _iter(resolved, recursive):
            if len(lines) >= MAX_ENTRIES:
                truncated += 1
                continue
            base = match_allowed_root(entry, self.allowed_roots) or self.root
            try:
                rel = entry.relative_to(base)
            except ValueError:
                rel = entry
            if entry.is_dir():
                lines.append(f"📁 {rel}/")
            else:
                size = entry.stat().st_size
                lines.append(f"📄 {rel}  ({size:,} o)")

        if truncated:
            lines.append(f"… ({truncated} entrées supplémentaires non affichées)")

        return "\n".join(lines) if lines else f"Répertoire vide: {path}"

    def diff_files(self, path_a: str, path_b: str) -> str:
        content_a = self.read_file(path_a)
        content_b = self.read_file(path_b)
        diff = difflib.unified_diff(
            content_a.splitlines(keepends=True),
            content_b.splitlines(keepends=True),
            fromfile=path_a,
            tofile=path_b,
        )
        result = "".join(diff)
        return result if result else "Les deux fichiers sont identiques."

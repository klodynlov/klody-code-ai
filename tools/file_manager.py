import difflib
import logging
from pathlib import Path

from config import MAX_FILE_SIZE, PROJECT_ROOT

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
    def __init__(self, root: Path = PROJECT_ROOT):
        self.root = root.resolve()

    # ------------------------------------------------------------------ #
    # Validation sandbox                                                   #
    # ------------------------------------------------------------------ #

    def _validate_path(self, path: str) -> Path:
        """
        Résout et valide un chemin relatif dans la sandbox.
        Lève SandboxViolation si le chemin sort du sandbox.
        """
        if not path or not path.strip():
            raise SandboxViolation("Chemin vide non autorisé")

        p = Path(path)
        if p.is_absolute():
            raise SandboxViolation(f"Chemin absolu interdit: {path}")

        resolved = (self.root / p).resolve()

        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise SandboxViolation(f"Chemin hors sandbox: '{path}' → '{resolved}'")

        # Symlink qui pointerait hors sandbox
        if resolved.exists() and resolved.is_symlink():
            link_target = resolved.resolve()
            try:
                link_target.relative_to(self.root)
            except ValueError:
                raise SandboxViolation(f"Symlink sortant du sandbox: '{path}' → '{link_target}'")

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
            raise IsADirectoryError(f"'{path}' est un répertoire, pas un fichier")

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

        existed = resolved.exists()
        resolved.write_text(content, encoding="utf-8")

        action = "modifié" if existed else "créé"
        logger.info("Écriture (%s): %s (%d caractères)", action, path, len(content))
        return f"Fichier {action} avec succès: {path}"

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
        }
        MAX_ENTRIES = 150

        def _iter(base: Path, recurse: bool):
            for entry in sorted(base.iterdir()):
                if entry.name in _SKIP_NAMES:
                    continue
                yield entry
                if recurse and entry.is_dir():
                    yield from _iter(entry, recurse)

        lines = []
        truncated = 0
        for entry in _iter(resolved, recursive):
            if len(lines) >= MAX_ENTRIES:
                truncated += 1
                continue
            rel = entry.relative_to(self.root)
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

import logging
import shutil
import subprocess
from pathlib import Path

from config import PROJECT_ROOT

logger = logging.getLogger(__name__)

HAS_RIPGREP: bool = shutil.which("rg") is not None
MAX_RESULTS = 100


class Search:
    def __init__(self, root: Path = PROJECT_ROOT):
        self.root = root.resolve()

    def search_in_files(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: str = "",
        case_sensitive: bool = True,
    ) -> str:
        """
        Recherche un pattern dans les fichiers du projet.
        Utilise ripgrep si disponible, sinon grep.
        """
        if not pattern.strip():
            return "ERREUR: Pattern de recherche vide"

        search_path = (self.root / path).resolve()
        try:
            search_path.relative_to(self.root)
        except ValueError:
            return f"ERREUR: Chemin hors sandbox: {path}"

        if not search_path.exists():
            return f"ERREUR: Chemin introuvable: {path}"

        if HAS_RIPGREP:
            return self._ripgrep(pattern, search_path, file_pattern, case_sensitive)
        return self._grep(pattern, search_path, file_pattern, case_sensitive)

    def _ripgrep(
        self, pattern: str, path: Path, file_pattern: str, case_sensitive: bool
    ) -> str:
        cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
        if not case_sensitive:
            cmd.append("--ignore-case")
        if file_pattern:
            cmd.extend(["--glob", file_pattern])
        cmd += ["--", pattern, str(path)]
        return self._run(cmd, pattern)

    def _grep(
        self, pattern: str, path: Path, file_pattern: str, case_sensitive: bool
    ) -> str:
        cmd = ["grep", "-rn", "--color=never"]
        if not case_sensitive:
            cmd.append("-i")
        if file_pattern:
            cmd.extend(["--include", file_pattern])
        cmd += ["--", pattern, str(path)]
        return self._run(cmd, pattern)

    def _run(self, cmd: list[str], pattern: str) -> str:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
            )

            # Code 1 = aucun résultat (comportement normal grep/rg)
            if result.returncode == 1 and not result.stdout.strip():
                return f"Aucun résultat pour: {pattern}"

            if result.returncode > 1:
                logger.error("Erreur recherche: %s", result.stderr)
                return f"ERREUR: {result.stderr.strip()}"

            lines = result.stdout.strip().splitlines()
            if len(lines) > MAX_RESULTS:
                surplus = len(lines) - MAX_RESULTS
                lines = lines[:MAX_RESULTS]
                lines.append(f"\n... ({surplus} lignes supplémentaires tronquées)")

            return "\n".join(lines) if lines else f"Aucun résultat pour: {pattern}"

        except subprocess.TimeoutExpired:
            return "ERREUR: Timeout de recherche (15s)"
        except FileNotFoundError as e:
            return f"ERREUR: Outil introuvable ({e})"
        except Exception as e:
            logger.error("Erreur recherche: %s", e)
            return f"ERREUR: {e}"

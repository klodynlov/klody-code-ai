"""Sandbox d'exécution pour Klody — venv jetable + feedback stderr.

Étape 3 de la roadmap v2.

Objectif : permettre à l'agent d'exécuter le code qu'il vient d'écrire et
de récupérer stderr/stdout/exit code en feedback, sans polluer le système.

Architecture :
- Un venv par projet (cache dans ~/.cache/klody/sandbox-venvs/<hash>/)
- Création paresseuse : on ne crée le venv que si on en a besoin
- Réutilisation entre sessions (pytest déjà installé, etc.)
- Auto-détection du bon command pour un .py donné (pytest si tests, python si main, py_compile sinon)
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from agent.dbc import ensure, require

logger = logging.getLogger(__name__)

# Taille max stdout/stderr renvoyés à l'agent (tronqué depuis la fin —
# les erreurs Python pertinentes sont toujours à la fin).
_MAX_OUTPUT_CHARS = 3000
# Timeout par défaut d'une exécution sandbox.
_DEFAULT_TIMEOUT = 30


@dataclass
class SandboxResult:
    """Résultat d'une exécution sandbox."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def format_for_llm(self) -> str:
        """Format compact pour réinjection dans le contexte du LLM."""
        status = "✅ PASS" if self.success else ("⏱ TIMEOUT" if self.timed_out else f"❌ FAIL (exit={self.exit_code})")
        lines = [f"$ {self.command}", f"  {status}  ({self.duration_s}s)"]
        if self.stdout.strip():
            lines.append("--- stdout ---")
            lines.append(self.stdout.strip())
        if self.stderr.strip():
            lines.append("--- stderr ---")
            lines.append(self.stderr.strip())
        return "\n".join(lines)


class SandboxRunner:
    """Gère un venv jetable par workdir + exécute des commandes Python isolées."""

    # Packages installés par défaut dans tout sandbox venv neuf.
    _DEFAULT_PACKAGES = ("pytest",)

    def __init__(self, workdir: Path, cache_root: Path | None = None):
        self.workdir = Path(workdir).resolve()
        if cache_root is None:
            cache_root = Path.home() / ".cache" / "klody" / "sandbox-venvs"
        # Un venv unique par workdir (hash du chemin absolu)
        digest = hashlib.sha256(str(self.workdir).encode()).hexdigest()[:16]
        self.venv_dir: Path = cache_root / digest
        self._ready = False

    # ------------------------------------------------------------------ #
    # Cycle de vie du venv                                                #
    # ------------------------------------------------------------------ #

    @property
    def python(self) -> Path:
        return self.venv_dir / "bin" / "python"

    @property
    def pip(self) -> Path:
        return self.venv_dir / "bin" / "pip"

    def ensure_venv(self) -> bool:
        """Crée le venv si absent + installe les packages de base.

        Retourne True si le venv est utilisable, False sinon (logge l'erreur).
        Idempotent.
        """
        if self._ready and self.python.exists():
            return True

        if not self.python.exists():
            self.venv_dir.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Création du venv sandbox: %s", self.venv_dir)
            try:
                subprocess.run(
                    [sys.executable, "-m", "venv", str(self.venv_dir)],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                logger.error("Échec création venv: %s", exc)
                return False

            # Install pytest et packages de base (silencieux)
            try:
                subprocess.run(
                    [str(self.pip), "install", "-q", *self._DEFAULT_PACKAGES],
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                logger.warning("Timeout installation packages de base — sandbox utilisable mais sans pytest")

        # Si un requirements.txt est présent dans le workdir, l'installer aussi.
        req = self.workdir / "requirements.txt"
        if req.exists():
            try:
                subprocess.run(
                    [str(self.pip), "install", "-q", "-r", str(req)],
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                logger.warning("Timeout installation requirements.txt — continue quand même")

        self._ready = True
        return True

    # ------------------------------------------------------------------ #
    # Exécution                                                            #
    # ------------------------------------------------------------------ #

    def run(self, command: list[str] | str, timeout: int = _DEFAULT_TIMEOUT) -> SandboxResult:
        """Exécute une commande dans le sandbox.

        - command : liste d'arguments (préféré) ou string (sera shell-splittée).
        - Le premier argument est ré-écrit vers le venv quand pertinent
          (python, python3, pytest, pip → venv).
        - cwd = self.workdir
        - Sortie tronquée à _MAX_OUTPUT_CHARS depuis la fin.
        """
        require(timeout > 0, f"timeout doit être strictement positif (reçu {timeout})")
        if isinstance(command, str):
            import shlex
            cmd = shlex.split(command)
        else:
            cmd = list(command)

        if not cmd:
            return SandboxResult(
                command="", exit_code=1,
                stdout="", stderr="commande vide", duration_s=0.0,
            )

        # Garantir le venv prêt
        if not self.ensure_venv():
            return SandboxResult(
                command=" ".join(cmd),
                exit_code=1,
                stdout="",
                stderr="impossible de créer le venv sandbox",
                duration_s=0.0,
            )

        # Réécrire le binaire pour utiliser le venv
        cmd = self._rewrite_to_venv(cmd)
        cmd_str = " ".join(cmd)

        t0 = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124  # convention shell
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            stderr = (stderr + f"\nTimeout après {timeout}s").lstrip()
        duration = round(time.monotonic() - t0, 2)
        ensure(duration >= 0, "durée d'exécution non négative")

        return SandboxResult(
            command=cmd_str,
            exit_code=exit_code,
            stdout=stdout[-_MAX_OUTPUT_CHARS:] if stdout else "",
            stderr=stderr[-_MAX_OUTPUT_CHARS:] if stderr else "",
            duration_s=duration,
            timed_out=timed_out,
        )

    def _rewrite_to_venv(self, cmd: list[str]) -> list[str]:
        """Remappe python/pytest/pip vers les binaires du venv."""
        if not cmd:
            return cmd
        first = cmd[0]
        if first in ("python", "python3"):
            return [str(self.python), *cmd[1:]]
        if first == "pytest":
            return [str(self.python), "-m", "pytest", *cmd[1:]]
        if first == "pip":
            return [str(self.pip), *cmd[1:]]
        # Sinon : commande système, on laisse tel quel
        return cmd


# ---------------------------------------------------------------------------- #
# Auto-détection : quelle commande lancer après write_file ?                    #
# ---------------------------------------------------------------------------- #


def auto_command_for(filepath: Path) -> list[str] | None:
    """Détermine la commande sandbox la plus pertinente pour valider ce fichier.

    Heuristiques :
    - .py + nom commence par `test_` OU contient `def test_` → pytest
    - .py + contient `if __name__ == "__main__":` → python <file>
    - .py sinon → py_compile (juste vérifier que ça parse)
    - tout le reste → None (pas d'auto-exec)
    """
    if filepath.suffix != ".py":
        return None
    try:
        src = filepath.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None

    name = filepath.name
    rel = str(filepath.name)  # nom relatif à workdir au point d'usage

    # Test file → pytest
    if name.startswith("test_") or name.endswith("_test.py") or "\ndef test_" in src or src.startswith("def test_"):
        return ["pytest", rel, "-q", "--no-header", "--tb=short"]

    # Entry point → python -c
    if 'if __name__ == "__main__"' in src or "if __name__ == '__main__'" in src:
        return ["python", rel]

    # Sinon : vérifie juste que le fichier parse (rapide, pas d'effet de bord)
    return ["python", "-m", "py_compile", rel]

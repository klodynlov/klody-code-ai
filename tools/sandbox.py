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
import os
import shlex
import signal
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
# Timeout des pip install (packages de base + requirements.txt). 120s suffisait
# pour pytest seul ; numpy/requests + des requirements lourds (torch, opencv)
# demandent de la marge — un dépassement coupait l'install EN SILENCE.
_PIP_TIMEOUT = 600


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
    # Volontairement léger (≈35 Mo) : le venv est multiplié par workdir.
    # Le reste (pandas, torch, opencv…) s'installe à la demande — règle
    # prompt base.md — ou via requirements.txt du projet.
    _DEFAULT_PACKAGES = ("pytest", "numpy", "requests")

    def __init__(self, workdir: Path, cache_root: Path | None = None):
        self.workdir = Path(workdir).resolve()
        if cache_root is None:
            cache_root = Path.home() / ".cache" / "klody" / "sandbox-venvs"
        # Un venv unique par workdir (hash du chemin absolu)
        digest = hashlib.sha256(str(self.workdir).encode()).hexdigest()[:16]
        self.venv_dir: Path = cache_root / digest
        self._ready = False
        # mtime du requirements.txt installé — None tant que rien d'installé.
        self._req_mtime: float | None = None

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

        Le requirements.txt du workdir est (ré)installé dès que son mtime
        change : l'agent écrit souvent ce fichier APRÈS le premier run — sans
        ce check, il était ignoré jusqu'au redémarrage du backend (le runner
        est caché par root dans l'orchestrator et `_ready` court-circuitait).

        Retourne True si le venv est utilisable, False sinon (logge l'erreur).
        Idempotent.
        """
        if not self.python.exists():
            self._ready = False
            self._req_mtime = None
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

            self._pip_install(list(self._DEFAULT_PACKAGES), label="packages de base")

        # requirements.txt : installer à l'apparition ET à chaque modification.
        req = self.workdir / "requirements.txt"
        req_mtime = req.stat().st_mtime if req.exists() else None
        if req_mtime is not None and req_mtime != self._req_mtime:
            self._pip_install(["-r", str(req)], label="requirements.txt")
        self._req_mtime = req_mtime

        self._ready = True
        return True

    def _pip_install(self, args: list[str], label: str) -> None:
        """pip install discret mais jamais muet : échec et timeout sont loggés.

        check=False voulu — un requirements cassé ne doit pas rendre le
        sandbox inutilisable, l'agent verra l'ImportError au run et corrigera.
        """
        try:
            res = subprocess.run(
                [str(self.pip), "install", "-q", *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=_PIP_TIMEOUT,
            )
            if res.returncode != 0:
                logger.warning(
                    "pip install %s en échec (exit=%s): %s",
                    label, res.returncode, (res.stderr or "")[-400:],
                )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Timeout installation %s (>%ss) — sandbox utilisable mais install incomplète",
                label, _PIP_TIMEOUT,
            )

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
            try:
                cmd = shlex.split(command)
            except ValueError as exc:
                # Guillemets non équilibrés : typiquement un SCRIPT multi-ligne
                # passé à run_in_sandbox au lieu d'une commande shell. On ne
                # laisse pas l'exception remonter (traceback non géré côté
                # orchestrator) — on rend un message actionnable.
                return SandboxResult(
                    command=command[:200],
                    exit_code=1,
                    stdout="",
                    stderr=(
                        f"commande shell non analysable ({exc}). run_in_sandbox attend "
                        "une COMMANDE (ex. `python script.py`), pas un script. Écris ton "
                        "code avec write_file puis lance `python <fichier>.py`."
                    ),
                    duration_s=0.0,
                )
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

        # PYTHONFAULTHANDLER=1 : sur SIGABRT, l'interpréteur dumpe la pile de
        # TOUS les threads sur stderr. Au timeout, on s'en sert pour montrer à
        # l'agent OÙ le code est resté coincé (deadlock, boucle infinie, I/O
        # bloquante) plutôt qu'un opaque « Timeout après Ns » qu'il ne peut pas
        # diagnostiquer (cf. session 419676b5 : deadlock sur threading.Lock
        # non-réentrant, l'agent a bouclé write→run→timeout sans jamais voir
        # la cause). stdin=DEVNULL : un input() ou une lecture bloquante échoue
        # immédiatement (EOFError) au lieu de pendre jusqu'au timeout.
        # MPLBACKEND=Agg : sandbox headless — sans ça, matplotlib tente le
        # backend macosx (fenêtre GUI) et plt.show() pend jusqu'au timeout.
        env = dict(os.environ, PYTHONFAULTHANDLER="1", MPLBACKEND="Agg")
        is_python = Path(cmd[0]).name.startswith(("python", "pytest"))

        t0 = time.monotonic()
        timed_out = False
        proc = subprocess.Popen(
            cmd,
            cwd=self.workdir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=env,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124  # convention shell
            if is_python:
                # Déclenche le dump faulthandler (pile de tous les threads sur
                # stderr) puis laisse un court instant au process pour l'écrire.
                try:
                    proc.send_signal(signal.SIGABRT)
                    stdout, stderr = proc.communicate(timeout=5)
                except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                    proc.kill()
                    stdout, stderr = proc.communicate()
            else:
                proc.kill()
                stdout, stderr = proc.communicate()
            hint = (f"Timeout après {timeout}s — le programme n'a pas rendu la main "
                    "(deadlock, boucle infinie ou I/O bloquante ?).")
            if is_python and "most recent call first" in (stderr or ""):
                hint += " Pile capturée ci-dessus (faulthandler) : repère la dernière " \
                        "frame de TON code pour localiser le blocage."
            stderr = ((stderr or "") + "\n" + hint).strip()
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

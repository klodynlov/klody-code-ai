"""Gestion des services externes (LibraryBrain, etc.) au démarrage de Klody."""
from __future__ import annotations

import atexit
import subprocess
import time
import logging
from pathlib import Path

import httpx
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

_librarybrain_proc: subprocess.Popen | None = None


def _is_up(base_url: str, timeout: float = 2.0) -> bool:
    """Vérifie si LibraryBrain répond."""
    try:
        httpx.get(f"{base_url}/api/stats", timeout=timeout)
        return True
    except Exception:
        return False


def _stop_librarybrain() -> None:
    global _librarybrain_proc
    if _librarybrain_proc and _librarybrain_proc.poll() is None:
        logger.info("Arrêt de LibraryBrain (PID %d)", _librarybrain_proc.pid)
        _librarybrain_proc.terminate()
        try:
            _librarybrain_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _librarybrain_proc.kill()
    _librarybrain_proc = None


def ensure_librarybrain(librarybrain_dir: str, librarybrain_url: str) -> bool:
    """
    Démarre LibraryBrain s'il n'est pas déjà actif.
    Retourne True si le service est disponible après l'opération.
    """
    global _librarybrain_proc

    # Extraire la base URL (sans /api/ask)
    base_url = librarybrain_url.rsplit("/api/", 1)[0]

    # Déjà up — rien à faire
    if _is_up(base_url):
        console.print("  [dim green]✓[/dim green]  [dim]LibraryBrain déjà actif[/dim]")
        return True

    if not librarybrain_dir:
        console.print(
            "  [yellow]⚠[/yellow]  LibraryBrain inaccessible. "
            "[dim]Ajouter LIBRARYBRAIN_DIR dans .env pour le démarrage auto.[/dim]"
        )
        return False

    lb_path = Path(librarybrain_dir)
    if not lb_path.exists():
        console.print(
            f"  [yellow]⚠[/yellow]  LIBRARYBRAIN_DIR introuvable : [dim]{librarybrain_dir}[/dim]"
        )
        return False

    console.print("  [dim]◎  Démarrage de LibraryBrain…[/dim]", end="")

    try:
        _librarybrain_proc = subprocess.Popen(
            [
                "python3", "-m", "uvicorn", "search.api:app",
                "--host", "127.0.0.1", "--port", "8765",
                "--log-level", "warning",
            ],
            cwd=str(lb_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(_stop_librarybrain)
        logger.info("LibraryBrain démarré (PID %d)", _librarybrain_proc.pid)
    except FileNotFoundError:
        console.print(" [red]python3 introuvable[/red]")
        return False
    except Exception as e:
        console.print(f" [red]Erreur : {e}[/red]")
        logger.error("Impossible de démarrer LibraryBrain: %s", e)
        return False

    # Attendre jusqu'à 20 s que le service réponde
    for _ in range(20):
        time.sleep(1)
        if _librarybrain_proc.poll() is not None:
            console.print(" [red]processus terminé prématurément[/red]")
            logger.error("LibraryBrain s'est arrêté immédiatement")
            return False
        if _is_up(base_url):
            console.print(" [green]✓[/green]")
            return True

    console.print(" [yellow]timeout[/yellow]")
    logger.warning("LibraryBrain n'a pas répondu dans les 20s")
    return False

"""Gestion des services externes (LibraryBrain, etc.) au démarrage de Klody."""
from __future__ import annotations

import atexit
import logging
import subprocess
import threading
import time
from pathlib import Path

import httpx
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

_librarybrain_proc: subprocess.Popen | None = None
_librarybrain_dir: str = ""
_librarybrain_base_url: str = ""
_librarybrain_status: dict = {"up": False, "pid": None, "restarts": 0}
_watchdog_thread: threading.Thread | None = None
_watchdog_stop = threading.Event()
# True si LibraryBrain tournait DÉJÀ au démarrage (géré par un service externe,
# p.ex. le LaunchAgent com.librarybrain.server avec KeepAlive). Dans ce cas Klody
# ne doit JAMAIS spawner son propre uvicorn : le port :8765 est déjà pris, le
# doublon meurt aussitôt (`[Errno 48] address already in use` → code=1) et le
# watchdog le comptait comme une « mort » — d'où 84 fausses morts en boucle.
_externally_managed: bool = False

_MAX_RESTARTS = 3
# stderr/stdout de LibraryBrain — sans ça (DEVNULL), les morts code=1 étaient
# indiagnostiquables. On capture en append pour garder l'historique des crashes.
_LB_LOG = Path(__file__).resolve().parent / "logs" / "librarybrain.log"


def _is_up(base_url: str, timeout: float = 2.0) -> bool:
    try:
        r = httpx.get(f"{base_url}/api/stats", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def get_librarybrain_status() -> dict:
    """Retourne l'état courant de LibraryBrain (thread-safe)."""
    global _librarybrain_proc
    pid = _librarybrain_proc.pid if _librarybrain_proc else None
    alive = _librarybrain_proc.poll() is None if _librarybrain_proc else False
    return {
        "up": _librarybrain_status["up"],
        "pid": pid if alive else None,
        "restarts": _librarybrain_status["restarts"],
    }


def _start_process(lb_path: Path) -> subprocess.Popen | None:
    """Lance le processus uvicorn LibraryBrain. Retourne le Popen ou None.

    stderr/stdout sont redirigés vers ``logs/librarybrain.log`` (append) : c'est
    la seule fenêtre sur les morts code=1, qui sont déclenchées par requête
    (cf. clusters de redémarrages à ~20-50s pendant l'usage). Le fd est dupliqué
    dans l'enfant par Popen, donc on peut refermer la copie parent aussitôt
    (pas de fuite de descripteur à chaque redémarrage).
    """
    try:
        _LB_LOG.parent.mkdir(parents=True, exist_ok=True)
        logf = open(_LB_LOG, "a")  # noqa: SIM115 — fd hérité par l'enfant, fermé juste après
        logf.write(f"\n===== [LibraryBrain] démarrage {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        logf.flush()
        try:
            proc = subprocess.Popen(
                [
                    "python3", "-m", "uvicorn", "search.api:app",
                    "--host", "127.0.0.1", "--port", "8765",
                    "--log-level", "warning",
                ],
                cwd=str(lb_path),
                stdout=logf,
                stderr=logf,
            )
        finally:
            logf.close()
        logger.info("[LibraryBrain] Démarré (PID %d) — logs → %s", proc.pid, _LB_LOG)
        return proc
    except Exception as e:
        logger.error("[LibraryBrain] Impossible de démarrer : %s", e)
        return None


def _watchdog() -> None:
    """Surveille LibraryBrain toutes les 15s et le redémarre si nécessaire.

    Budget de _MAX_RESTARTS tentatives CONSÉCUTIVES. Une fois épuisé, on
    signale l'abandon UNE SEULE fois puis on bascule en surveillance passive :
    plus aucun redémarrage, et surtout plus aucun log (avant ce correctif, la
    branche « abandon » se ré-exécutait toutes les 15s et noyait agent.log —
    ~300 lignes par session morte). Le budget se réarme automatiquement si le
    service redevient joignable (reprise manuelle), pour ne pas rester aveugle.
    """
    global _librarybrain_proc

    lb_path = Path(_librarybrain_dir)
    abandoned = False  # budget épuisé ET abandon déjà signalé

    while not _watchdog_stop.is_set():
        _watchdog_stop.wait(15)
        if _watchdog_stop.is_set():
            break

        alive = _librarybrain_proc and _librarybrain_proc.poll() is None
        reachable = _is_up(_librarybrain_base_url)
        _librarybrain_status["up"] = reachable

        if reachable:
            # Service sain : réarme le budget (les échecs comptés doivent être
            # CONSÉCUTIFS) et lève l'abandon si on l'avait déclaré.
            if _librarybrain_status["restarts"] or abandoned:
                logger.info("[LibraryBrain] De nouveau joignable — surveillance réarmée.")
                _librarybrain_status["restarts"] = 0
                abandoned = False
            continue

        # Injoignable, et géré par un service externe (launchd) : surtout NE PAS
        # spawner de doublon — le port est à lui, notre uvicorn mourrait sur
        # `[Errno 48]`. On observe et on laisse le gestionnaire externe relancer.
        if _externally_managed:
            if not abandoned:
                abandoned = True
                logger.warning(
                    "[LibraryBrain] Injoignable — géré en externe (launchd), "
                    "pas de redémarrage par Klody (reprise auto attendue)."
                )
            continue

        # Injoignable.
        if _librarybrain_status["restarts"] < _MAX_RESTARTS:
            # Processus mort ou injoignable — tenter un redémarrage
            if _librarybrain_proc and not alive:
                logger.warning("[LibraryBrain] Processus mort (code=%s) — redémarrage…",
                               _librarybrain_proc.returncode)
            else:
                logger.warning("[LibraryBrain] Injoignable — redémarrage…")

            _librarybrain_proc = _start_process(lb_path)
            if _librarybrain_proc:
                _librarybrain_status["restarts"] += 1
                # Attendre jusqu'à 15s que le service réponde
                for _ in range(15):
                    time.sleep(1)
                    if _is_up(_librarybrain_base_url):
                        _librarybrain_status["up"] = True
                        logger.info("[LibraryBrain] Redémarrage réussi (tentative %d)",
                                    _librarybrain_status["restarts"])
                        break
        elif not abandoned:
            # Budget épuisé : on signale l'abandon UNE fois puis on reste en
            # surveillance passive (ni redémarrage, ni spam).
            abandoned = True
            logger.error(
                "[LibraryBrain] %d redémarrages consécutifs échoués — abandon "
                "(surveillance passive, reprise auto si le service revient).",
                _MAX_RESTARTS,
            )


def _stop_librarybrain() -> None:
    global _librarybrain_proc
    _watchdog_stop.set()
    if _librarybrain_proc and _librarybrain_proc.poll() is None:
        logger.info("[LibraryBrain] Arrêt (PID %d)", _librarybrain_proc.pid)
        _librarybrain_proc.terminate()
        try:
            _librarybrain_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _librarybrain_proc.kill()
    _librarybrain_proc = None
    _librarybrain_status["up"] = False


def ensure_librarybrain(librarybrain_dir: str, librarybrain_url: str) -> bool:
    """
    Démarre LibraryBrain si nécessaire, puis lance un watchdog de surveillance.
    Retourne True si le service est disponible.
    """
    global _librarybrain_proc, _librarybrain_dir, _librarybrain_base_url
    global _watchdog_thread, _externally_managed

    base_url = librarybrain_url.rsplit("/api/", 1)[0]
    _librarybrain_dir = librarybrain_dir
    _librarybrain_base_url = base_url

    # Déjà up : c'est un service externe (launchd) qui le gère. Klody se contente
    # de surveiller — il ne spawnera jamais de doublon (cf. _externally_managed).
    if _is_up(base_url):
        _librarybrain_status["up"] = True
        _externally_managed = True
        console.print("  [dim green]✓[/dim green]  [dim]LibraryBrain déjà actif (géré en externe)[/dim]")
        _launch_watchdog()
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

    _librarybrain_proc = _start_process(lb_path)
    if _librarybrain_proc is None:
        console.print(" [red]échec[/red]")
        return False

    atexit.register(_stop_librarybrain)

    # Attendre jusqu'à 20s
    for _ in range(20):
        time.sleep(1)
        if _librarybrain_proc.poll() is not None:
            console.print(" [red]processus terminé prématurément[/red]")
            return False
        if _is_up(base_url):
            _librarybrain_status["up"] = True
            console.print(" [green]✓[/green]")
            _launch_watchdog()
            return True

    console.print(" [yellow]timeout[/yellow]")
    logger.warning("[LibraryBrain] N'a pas répondu dans les 20s")
    _launch_watchdog()  # Watchdog surveille quand même
    return False


def _launch_watchdog() -> None:
    global _watchdog_thread
    if _watchdog_thread and _watchdog_thread.is_alive():
        return
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(target=_watchdog, daemon=True, name="lb-watchdog")
    _watchdog_thread.start()
    logger.debug("[LibraryBrain] Watchdog démarré")

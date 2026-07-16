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

# États de la sonde. « Joignable » et « exploitable » sont deux choses distinctes :
# quand `api_token` est défini dans le config.yaml de LibraryBrain, son middleware
# (api/auth.py) répond 401 sur tout /api/ — le service tourne, mais AUCUN appel de
# Klody n'aboutit (Klody n'envoie aucun X-API-Token). L'ancienne sonde booléenne
# (`status_code < 500`) écrasait cette nuance et publiait un point vert menteur :
# /status CONFIRMAIT la panne au lieu de la lever.
PROBE_UP = "up"                      # 200 : joignable ET autorisé
PROBE_UNAUTHORIZED = "unauthorized"  # 401/403 : joignable, mais Klody est refusé
PROBE_DOWN = "down"                  # injoignable, ou réponse inexploitable

# Nommer `api_token` : une panne d'auth doit se lire comme une panne d'auth.
_UNAUTHORIZED_DETAIL = (
    "401 sur /api/ — `api_token` est défini dans le config.yaml de LibraryBrain "
    "et Klody n'envoie aucun token. Vider `api_token` (usage local), ou câbler "
    "l'en-tête X-API-Token côté Klody."
)
_STATE_DETAIL = {PROBE_UNAUTHORIZED: _UNAUTHORIZED_DETAIL}

_librarybrain_proc: subprocess.Popen | None = None
_librarybrain_dir: str = ""
_librarybrain_base_url: str = ""
# `up` n'est PAS stocké ici : il est dérivé de `state` dans
# get_librarybrain_status(). Un seul état publiable ⇒ aucun chemin de code ne peut
# republier « up=True » sur un service qui refuse Klody.
_librarybrain_status: dict = {"state": PROBE_DOWN, "pid": None, "restarts": 0}
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


def _probe(base_url: str, timeout: float = 2.0) -> str:
    """Sonde LibraryBrain → PROBE_UP / PROBE_UNAUTHORIZED / PROBE_DOWN.

    Le 200 est STRICT. `/api/stats` est une route authentifiée : un 401/403 dit
    « le port répond mais Klody est refusé » — ni vivant, ni mort. Sonder
    `/health` à la place ne réparerait rien : il est exempté d'auth
    (_EXEMPT_PREFIXES dans api/auth.py) et répondrait 200 alors que tout /api/
    est fermé — le verdict resterait aussi faux, juste par un autre chemin.
    """
    try:
        r = httpx.get(f"{base_url}/api/stats", timeout=timeout)
    except Exception:
        return PROBE_DOWN
    if r.status_code == 200:
        return PROBE_UP
    if r.status_code in (401, 403):
        return PROBE_UNAUTHORIZED
    return PROBE_DOWN


def get_librarybrain_status() -> dict:
    """Retourne l'état courant de LibraryBrain (thread-safe).

    `up` est DÉRIVÉ de `state`, jamais stocké : c'est ce qui garantit
    structurellement qu'un 401 ne puisse plus ressortir en « up ». `detail`
    porte le message actionnable pour l'UI et /status.
    """
    global _librarybrain_proc
    pid = _librarybrain_proc.pid if _librarybrain_proc else None
    alive = _librarybrain_proc.poll() is None if _librarybrain_proc else False
    state = _librarybrain_status["state"]
    return {
        "up": state == PROBE_UP,
        "state": state,
        "detail": _STATE_DETAIL.get(state, ""),
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
        # `with` : le fd est dupliqué dans l'enfant par Popen, donc refermer la
        # copie parent en sortie de bloc est sûr (l'enfant garde la sienne) et
        # garantit la fermeture même si write/flush/Popen lève (pas de fuite).
        with open(_LB_LOG, "a") as logf:
            logf.write(f"\n===== [LibraryBrain] démarrage {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            logf.flush()
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

    Un service qui refuse Klody (401) n'est PAS un service mort : il est signalé
    une fois puis surveillé passivement, jamais redémarré.
    """
    global _librarybrain_proc

    lb_path = Path(_librarybrain_dir)
    abandoned = False  # budget épuisé ET abandon déjà signalé
    auth_warned = False  # refus d'auth déjà signalé (log unique, comme abandoned)

    while not _watchdog_stop.is_set():
        _watchdog_stop.wait(15)
        if _watchdog_stop.is_set():
            break

        alive = _librarybrain_proc and _librarybrain_proc.poll() is None
        state = _probe(_librarybrain_base_url)
        _librarybrain_status["state"] = state

        if state == PROBE_UP:
            # Service sain : réarme le budget (les échecs comptés doivent être
            # CONSÉCUTIFS) et lève l'abandon si on l'avait déclaré.
            if _librarybrain_status["restarts"] or abandoned or auth_warned:
                logger.info(
                    "[LibraryBrain] De nouveau %s — surveillance réarmée.",
                    "autorisé" if auth_warned else "joignable",
                )
                _librarybrain_status["restarts"] = 0
                abandoned = False
                auth_warned = False
            continue

        if state == PROBE_UNAUTHORIZED:
            # Le process TOURNE et tient :8765. Le redémarrer ne réparerait rien
            # (panne de config, pas de process), et spawner un doublon sur un port
            # déjà pris rejouerait les 84 fausses morts `[Errno 48]`. On signale
            # une fois, on surveille — le budget de redémarrages n'est pas touché.
            if not auth_warned:
                auth_warned = True
                logger.error("[LibraryBrain] %s", _UNAUTHORIZED_DETAIL)
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
                    state = _probe(_librarybrain_base_url)
                    if state == PROBE_DOWN:
                        continue
                    # Reparti. S'il refuse Klody, le cycle suivant le signalera :
                    # ne pas crier « réussi » sur un service qui n'accepte rien.
                    _librarybrain_status["state"] = state
                    if state == PROBE_UP:
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
    _librarybrain_status["state"] = PROBE_DOWN


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

    # Déjà up (ou en cours de démarrage) : un service externe (launchd) le gère.
    # Klody se contente de surveiller — il ne spawnera jamais de doublon (cf.
    # _externally_managed).
    #
    # Fenêtre de grâce au boot : le LaunchAgent com.librarybrain.server (RunAtLoad
    # + KeepAlive) met quelques secondes à binder :8765 (chargement de l'index
    # FTS5). Sans grâce, on voit le port encore libre → on spawne NOTRE uvicorn →
    # course → doublon tué aussitôt sur « [Errno 48] address already in use »
    # (exactement le même TOCTOU que l'API :8000 et le worker MLX :8080). On laisse
    # donc au propriétaire externe une fenêtre pour se présenter avant de décider
    # de démarrer nous-mêmes. Cette attente ne bloque pas le boot de l'API : le
    # lifespan appelle ensure_librarybrain dans un thread détaché (cf. server.py).
    for _ in range(8):
        state = _probe(base_url, timeout=1.0)
        if state != PROBE_DOWN:
            # Le port :8765 a un propriétaire externe — que Klody soit autorisé ou
            # non. Dans les DEUX cas il ne faut jamais spawner de doublon.
            _externally_managed = True
            _librarybrain_status["state"] = state
            _launch_watchdog()
            if state == PROBE_UP:
                console.print("  [dim green]✓[/dim green]  [dim]LibraryBrain déjà actif (géré en externe)[/dim]")
                return True
            console.print("  [yellow]⚠[/yellow]  LibraryBrain actif mais [bold]refuse Klody[/bold] (401)")
            console.print(f"     [dim]{_UNAUTHORIZED_DETAIL}[/dim]")
            return False
        time.sleep(1)

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
        state = _probe(base_url)
        if state != PROBE_DOWN:
            _librarybrain_status["state"] = state
            _launch_watchdog()
            if state == PROBE_UP:
                console.print(" [green]✓[/green]")
                return True
            # Démarré par nous, mais son config.yaml porte un api_token : le
            # process est sain, la liaison ne l'est pas. Ne pas annoncer « ✓ ».
            console.print(" [yellow]⚠ démarré mais refuse Klody (401)[/yellow]")
            console.print(f"     [dim]{_UNAUTHORIZED_DETAIL}[/dim]")
            return False

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

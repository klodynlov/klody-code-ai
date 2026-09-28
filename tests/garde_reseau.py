"""La suite ne DOIT JOINDRE AUCUN service de la machine — garde de SUITE.

Jumeau réseau de `tests/garde_etat.py`, pour la même raison : un correctif
local protège le fichier corrigé, un garde de suite protège le prochain.

Constaté le 2026-09-27, sonde d'audit `socket.connect` qui REFUSE (donc sans
rien verser en prod pendant la mesure) :

- `tests/integration` sur `main` : **671 tentatives** vers :8765 (LibraryBrain,
  thread `lb-init`), :8090 (gateway ET journal d'usage, `klody-journal-client`),
  :11434 (Ollama, `mem-extractor`) et :8087 (MCP Klody). Réglé dossier par
  dossier par les deux PR précédentes, qui ont posé un garde NOMMÉ dans
  `tests/integration/conftest.py` ;
- le reste de la suite, ces deux PR comprises : **44 tentatives**, hors de
  portée de ce garde-là. 40 vers le journal d'usage :8090 (`test_audio_wiring`,
  `test_excel`, `test_documents`, `test_garde_origine`, `test_sessions_et_boucle`,
  `test_archive`, `test_journal_client`…), 4 vers Ollama via `/api/status`
  (`test_health_peremption`). Chaque événement du journal arrive en prod comme
  `source='user'` : le miner d'habitudes les prend pour l'utilisateur.

Le périmètre n'est PAS une liste de services : c'est tout le loopback sous le
premier port éphémère. Une liste ne voit que ce qu'on y a mis — le jour où un
service de l'atelier s'ajoute (:8766 local-suno, :8899 aperçu, un serveur MCP
de plus), un garde nommé resterait vert sur sa fuite. Ici, tout ce qui écoute
en local sur un port FIXE est hors d'atteinte ; les serveurs qu'un test ouvre
lui-même (`bind(("127.0.0.1", 0))`) tombent dans la plage éphémère et passent.

Mécanisme : un hook d'audit (PEP 578) sur `socket.connect`, levé par le C pour
`connect` comme pour `connect_ex`. Il est insensible à l'ordre des
`monkeypatch` et au chemin emprunté (`socket.socket`, `_socket.socket`,
asyncio, httpx, urllib) : il REFUSE avant toute connexion et CONSIGNE. Refuser
ne suffit pas — les appels fautifs partent de threads démons
(`klody-journal-client`, `mem-extractor`, `lb-init`) qui avalent l'erreur, le
test resterait vert. La consignation est relue après chaque test par une
fixture autouse (`tests/conftest.py`) qui le fait rougir.

⚠️ `connect_ex` LÈVE au lieu de rendre un code d'erreur quand le hook refuse :
le C abandonne l'appel dès que l'audit échoue. Aucun code de production ne
l'utilise (vérifié le 2026-09-27) ; le garde nommé des tests d'intégration,
qui passe AVANT (niveau Python), garde la sémantique `ECONNREFUSED`.

Limites connues : un sous-processus n'est pas couvert (son propre conftest
l'est s'il relance pytest) ; une tentative entre deux tests est refusée mais
imputée à personne ; un service local qui écouterait AU-DESSUS de 32767 n'est
pas couvert ; un nom d'hôte autre que `localhost` passé brut à `connect` n'est
pas résolu (`create_connection`, le chemin de httpx et d'urllib, résout avant).

> Un garde-fou qui ne peut pas rougir est indiscernable d'un garde-fou vert.
  (CLAUDE.md.) Verrouillé par `tests/test_hermeticite_reseau.py`.

⚠️ Ce module ne doit RIEN importer du projet au chargement : il est posé avant
`config`, comme `garde_etat`.
"""
from __future__ import annotations

import errno
import ipaddress
import sys
import threading
from urllib.parse import urlsplit

import httpx

# Premier port de la plage éphémère LA PLUS BASSE des deux systèmes : Linux
# (défaut `ip_local_port_range` 32768-60999, le runner CI) et macOS
# (`net.inet.ip.portrange.first` = 49152, mesuré sur le Mac le 2026-09-27). Un
# serveur qu'un test ouvre sur le port 0 atterrit au-dessus sur les deux ;
# tous les services de l'atelier écoutent en dessous (8000-8899, 11434).
PREMIER_PORT_EPHEMERE = 32768

_violations: list[str] = []
_verrou = threading.Lock()
# Un conteneur plutôt qu'un booléen `global` : un addaudithook est définitif
# (PEP 578), un second appel doublerait chaque refus.
_hooks_poses: list[object] = []
# Position du dernier relevé (`a_relire`) : un conteneur, comme ci-dessus.
_releve = [0]

_BOUCLE_LOCALE = frozenset({"127.0.0.1", "localhost", "::1"})


def est_boucle_locale(hote: str) -> bool:
    """Vrai si `hote` désigne CETTE machine : loopback, `localhost`, ou l'adresse
    non spécifiée (`0.0.0.0` et `::` joignent les services locaux)."""
    hote = hote.split("%", 1)[0]  # zone IPv6 : « fe80::1%lo0 »
    if hote.lower() == "localhost" or hote.lower().endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(hote)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # « ::ffff:127.0.0.1 »
    return ip.is_loopback or ip.is_unspecified


def cible_interdite(adresse: object) -> bool:
    """Une adresse de `connect` vise-t-elle un service FIXE de la machine ?"""
    if not (isinstance(adresse, tuple) and len(adresse) >= 2):
        return False  # socket Unix (chemin) : hors périmètre
    hote, port = adresse[0], adresse[1]
    if not (isinstance(hote, str) and isinstance(port, int)):
        return False
    return port < PREMIER_PORT_EPHEMERE and est_boucle_locale(hote)


def services_de_la_machine() -> dict[int, tuple[str, frozenset[str]]]:
    """Port → (services, hôtes) de ce qui tourne sur la machine de dev.

    Dérivé de la configuration, pas écrit en dur : un `.env` qui déplace le
    gateway déplace le garde avec lui. Plusieurs services peuvent partager un
    port (en mode gateway, le modèle code et le worker VL passent par :8090).
    """
    import config
    from agent import journal_client

    urls = {
        "LibraryBrain": config.LIBRARYBRAIN_URL,
        "gateway klody-core": config.MLX_BASE_URL,
        "journal d'usage": journal_client.gateway_root(),
        "modèle code": config.MLX_CODE_BASE_URL,
        "worker VL": config.VL_BASE_URL,
        "Ollama": config.OLLAMA_BASE_URL,
        "MCP Klody": config.KLODY_MCP_URL,
    }
    services: dict[int, tuple[list[str], set[str]]] = {}
    for nom, url in urls.items():
        cible = urlsplit(url)
        if not cible.hostname:
            continue
        port = cible.port or (443 if cible.scheme == "https" else 80)
        noms, hotes = services.setdefault(port, ([], set(_BOUCLE_LOCALE)))
        if nom not in noms:
            noms.append(nom)
        hotes.add(cible.hostname)
    return {port: (" / ".join(noms), frozenset(hotes)) for port, (noms, hotes) in services.items()}


def _nommer(port: int) -> str:
    """Le service connu à ce port, pour que le message dise QUOI a été visé."""
    try:
        return services_de_la_machine()[port][0]
    except Exception:  # config pas encore importable, port inconnu…
        return "service local non répertorié"


def _hook(evenement: str, args: tuple) -> None:
    # Appelé pour CHAQUE événement d'audit du processus : sortir au plus vite.
    if evenement != "socket.connect":
        return
    adresse = args[1]
    if not cible_interdite(adresse):
        return
    hote, port = adresse[0], adresse[1]
    nom = _nommer(port)
    message = f"connexion vers {hote}:{port} [{nom}] (fil « {threading.current_thread().name} »)"
    with _verrou:
        _violations.append(message)
    raise ConnectionRefusedError(
        errno.ECONNREFUSED,
        f"[tests] connexion REFUSÉE vers un service de la machine : {hote}:{port} "
        f"({nom}). Un test ne joint que ses propres serveurs, sur un port "
        "éphémère — cf. tests/garde_reseau.py.",
    )


def installer_garde() -> None:
    """Pose le hook d'audit, une seule fois par processus."""
    if _hooks_poses:
        return
    sys.addaudithook(_hook)
    _hooks_poses.append(_hook)


def a_relire() -> list[str]:
    """Tout ce qui a été consigné depuis le DERNIER relevé, et avance le relevé.

    Pas « depuis le début du test » : le worker du journal vide sa file en
    différé, et ses tentatives tombent souvent ENTRE deux tests — pendant le
    setup du suivant, avant qu'une marque de début ait été prise. Mesuré le
    2026-09-27, mutation « journal non coupé » hors `tests/integration` : avec
    une marque au setup, 2 tests rougissaient sur les ~40 tentatives que la
    sonde imputait à `test_audio_wiring`, `test_excel`… — les autres étaient
    refusées, mais imputées à PERSONNE. Relevé de fin à fin : rien ne tombe
    entre deux fenêtres, au prix d'une imputation au test suivant (le nom du
    fil dans le message dit d'où vient la tentative).
    """
    with _verrou:
        debut = min(_releve[0], len(_violations))
        fuites = list(_violations[debut:])
        _releve[0] = len(_violations)
    return fuites


def marque() -> int:
    """Position courante du registre : ce qui sera consigné après appartient au test."""
    with _verrou:
        return len(_violations)


def violations_depuis(position: int) -> list[str]:
    with _verrou:
        return list(_violations[position:])


def oublier_depuis(position: int) -> None:
    """Réservé à l'auto-test du garde : efface ce qu'il vient de provoquer exprès."""
    with _verrou:
        del _violations[position:]


class HttpxSansReseau:
    """`httpx` tel que le voit `api/server.py` : ses `AsyncClient` ne sortent pas.

    Tout le reste est le vrai module. Chaque requête lève `httpx.ConnectError`,
    ce que rend un service absent (le chemin de la CI), et son URL est gardée
    dans `requetes`. Pour les routes qui ouvrent leur propre client
    (`/api/status`, `/health`, `/api/proposals`) : le garde les refuserait de
    toute façon, ce double leur donne le chemin « service absent » SANS faire
    rougir le test.
    """

    def __init__(self) -> None:
        self.requetes: list[str] = []

    def __getattr__(self, nom: str):
        return getattr(httpx, nom)

    # Même nom que dans httpx : c'est `httpx.AsyncClient(...)` qu'appelle l'API.
    def AsyncClient(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(self._refuser)
        return httpx.AsyncClient(*args, **kwargs)

    def _refuser(self, request: httpx.Request) -> httpx.Response:
        self.requetes.append(f"{request.method} {request.url}")
        raise httpx.ConnectError(
            "service de la machine coupé pendant les tests", request=request
        )

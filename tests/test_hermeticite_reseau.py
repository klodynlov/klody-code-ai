"""La suite ne joint aucun service de la machine — et le garde qui l'assure sait rougir.

Jumeau de `test_hermeticite_etat_persistant.py`, pour la même raison :
`tests/garde_reseau.py` et la fixture `_services_de_la_machine_injoignables`
doivent pouvoir TOMBER. Sans ce fichier, une régression qui retirerait le hook
ou la relecture laisserait la suite verte — et le seul signe serait, de
nouveau, des événements `source='user'` dans le journal d'usage de klody-core,
c'est-à-dire rien que pytest regarde.

⚠️ Aucun test ici ne vise un vrai service : le port « interdit » est le 9
(discard), où rien n'écoute. Garde retiré, la connexion y est refusée par le
SYSTÈME, sans le préfixe « [tests] » du garde — c'est ce préfixe qui départage.
"""
from __future__ import annotations

import _socket
import asyncio
import os
import socket
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from urllib.parse import urlsplit

import config
import pytest
from agent import journal_client

from tests import garde_reseau

REPO = Path(__file__).resolve().parent.parent
PORT_INTERDIT = 9  # discard : rien n'y écoute, garde retiré ou non


@pytest.fixture
def registre():
    """Position d'entrée du registre, remise en place à la sortie : les refus
    provoqués ici sont VOULUS, la fixture autouse ne doit pas les reprocher."""
    position = garde_reseau.marque()
    yield position
    garde_reseau.oublier_depuis(position)


def _refus_du_garde(exc: BaseException | None) -> bool:
    return isinstance(exc, ConnectionRefusedError) and "[tests]" in str(exc)


# --- le périmètre ---------------------------------------------------------------


def test_le_seuil_est_le_premier_port_ephemere_de_linux():
    """Verrouillé sur un LITTÉRAL : un test qui relirait la constante suivrait
    le réglage au lieu de le juger (mutation déjà échappée une fois, veille Qwen).
    Au-dessus de 32768, un serveur ouvert par un test sur le runner Linux
    (plage 32768-60999) serait refusé ; en dessous, :8765 ou :11434 passeraient."""
    assert garde_reseau.PREMIER_PORT_EPHEMERE == 32768


@pytest.mark.parametrize(
    ("adresse", "interdite"),
    [
        (("127.0.0.1", 8090), True),
        (("127.0.0.53", 8090), True),  # tout 127/8 est loopback
        (("::1", 11434, 0, 0), True),
        (("::ffff:127.0.0.1", 8765, 0, 0), True),  # IPv4 dans IPv6
        (("localhost", 8000), True),
        (("0.0.0.0", 8899), True),  # joint les services locaux
        (("::", 8087, 0, 0), True),
        (("127.0.0.1", 32767), True),
        (("127.0.0.1", 32768), False),  # plage éphémère : serveur ouvert par le test
        (("127.0.0.1", 53168), False),
        (("93.184.216.34", 443), False),  # hors machine : hors périmètre
        (("exemple.org", 80), False),
        ("/tmp/klody.sock", False),  # socket Unix
    ],
)
def test_cible_interdite(adresse, interdite):
    assert garde_reseau.cible_interdite(adresse) is interdite


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(lambda: config.MLX_BASE_URL, id="gateway"),
        pytest.param(journal_client.gateway_root, id="journal"),
        pytest.param(lambda: config.MLX_CODE_BASE_URL, id="modele-code"),
        pytest.param(lambda: config.VL_BASE_URL, id="worker-vl"),
        pytest.param(lambda: config.OLLAMA_BASE_URL, id="ollama"),
        pytest.param(lambda: config.LIBRARYBRAIN_URL, id="librarybrain"),
        pytest.param(lambda: config.KLODY_MCP_URL, id="mcp-klody"),
    ],
)
def test_chaque_service_de_la_config_est_dans_le_perimetre(url):
    """Si un service quittait le loopback ou montait au-dessus de 32767, ce
    garde ne le couvrirait plus : autant le savoir ici que par une fuite."""
    cible = urlsplit(url())
    port = cible.port or (443 if cible.scheme == "https" else 80)
    assert garde_reseau.cible_interdite((cible.hostname, port)), cible.geturl()


def test_le_refus_nomme_le_service_vise():
    port = urlsplit(config.MLX_BASE_URL).port
    assert "gateway klody-core" in garde_reseau._nommer(port)
    assert garde_reseau._nommer(PORT_INTERDIT) == "service local non répertorié"


# --- le garde refuse ET consigne, quel que soit le chemin -----------------------


def test_une_connexion_depuis_un_thread_demon_est_refusee_et_consignee(registre):
    """Le cas réel : `klody-journal-client`, `mem-extractor`, `lb-init`."""
    erreurs: list[BaseException] = []

    def _joindre() -> None:
        try:
            socket.create_connection(("127.0.0.1", PORT_INTERDIT), timeout=1).close()
        except OSError as exc:
            erreurs.append(exc)

    fil = threading.Thread(target=_joindre, name="demon-du-test", daemon=True)
    fil.start()
    fil.join(timeout=5)

    assert erreurs and _refus_du_garde(erreurs[0]), erreurs
    [violation] = garde_reseau.violations_depuis(registre)
    assert f"127.0.0.1:{PORT_INTERDIT}" in violation
    assert "demon-du-test" in violation


def test_le_socket_c_brut_ne_contourne_pas_le_garde(registre):
    """Un `monkeypatch` de `socket.socket.connect` ne voit pas `_socket.socket` ;
    le hook d'audit, levé par le C, si."""
    brut = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError, match=r"\[tests\]"):
            brut.connect(("127.0.0.1", PORT_INTERDIT))
    finally:
        brut.close()
    assert len(garde_reseau.violations_depuis(registre)) == 1


def test_connect_ex_est_couvert_aussi(registre):
    """Sémantique documentée : le C abandonne `connect_ex` quand l'audit échoue,
    il LÈVE au lieu de rendre un code. Aucun code de production ne l'utilise."""
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s,
        pytest.raises(ConnectionRefusedError, match=r"\[tests\]"),
    ):
        s.connect_ex(("127.0.0.1", PORT_INTERDIT))
    assert len(garde_reseau.violations_depuis(registre)) == 1


def test_le_chemin_asyncio_est_couvert(registre):
    """Celui de httpx en asynchrone, donc des sondes de `/api/status`."""
    async def _ouvrir():
        await asyncio.open_connection("127.0.0.1", PORT_INTERDIT)

    with pytest.raises(OSError) as exc:
        asyncio.run(_ouvrir())
    assert "[tests]" in str(exc.value), exc.value
    assert garde_reseau.violations_depuis(registre)


def test_un_serveur_ouvert_par_le_test_passe(registre):
    """Le garde ne coupe pas tout le réseau : ce qu'un test ouvre lui-même, sur
    un port éphémère, reste joignable."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as serveur:
        serveur.bind(("127.0.0.1", 0))
        serveur.listen(1)
        port = serveur.getsockname()[1]
        assert port >= garde_reseau.PREMIER_PORT_EPHEMERE
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    assert garde_reseau.violations_depuis(registre) == []


# --- de bout en bout : un test fautif ROUGIT ------------------------------------


def _session_pytest(tmp_path: Path, fichiers: dict[str, str]) -> tuple[int, str]:
    """Une vraie session pytest sur `fichiers`, `tests/conftest.py` chargé comme
    plugin : la fixture autouse et le bilan de session, pas des doubles."""
    for nom, source in fichiers.items():
        (tmp_path / nom).write_text(textwrap.dedent(source), encoding="utf-8")
    # L'environnement du test passe TEL QUEL à l'enfant, dossier d'état jetable
    # compris : `garde_etat` sait reconnaître un pytest fils depuis #294.
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider",
         "-q", "--no-header", *sorted(fichiers)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


# Un fil démon qui joint un port fixe en AVALANT l'erreur, exactement comme le
# worker du journal d'usage.
_JOINDRE_EN_DOUCE = f"""
import socket
import threading


def joindre_en_douce(nom_du_fil):
    def _joindre():
        try:
            socket.create_connection(("127.0.0.1", {PORT_INTERDIT}), timeout=1)
        except OSError:
            pass

    fil = threading.Thread(target=_joindre, name=nom_du_fil, daemon=True)
    fil.start()
    fil.join(timeout=5)
"""


def test_un_test_qui_joint_un_service_de_la_machine_fait_rougir_la_suite(tmp_path):
    """Deux tests : l'un propre (doit passer — sinon l'échec viendrait du
    harnais, pas du garde), l'autre qui joint un port fixe depuis un fil démon."""
    code, sortie = _session_pytest(tmp_path, {
        "test_fautif.py": _JOINDRE_EN_DOUCE + """

def test_propre():
    assert True


def test_joint_en_douce():
    joindre_en_douce("fil-fautif")
""",
    })

    assert code == 1, sortie
    # Le corps du test fautif PASSE (l'erreur est avalée) : c'est le démontage
    # qui le fait tomber — pytest le compte donc « passed » ET « error ».
    assert "1 error" in sortie, sortie
    assert "ERROR test_fautif.py::test_joint_en_douce" in sortie, sortie
    assert "ERROR test_fautif.py::test_propre" not in sortie, sortie
    assert f"127.0.0.1:{PORT_INTERDIT}" in sortie, sortie
    assert "fil-fautif" in sortie, sortie


def test_une_tentative_entre_deux_tests_rougit_le_suivant(tmp_path):
    """Le cas du worker du journal, qui vide sa file en différé : la tentative
    tombe entre la fin d'un test et le début du suivant. Rendue déterministe
    par une fixture de MODULE, démontée après le dernier test du module et
    avant le premier du module suivant. Avec un relevé pris au début de chaque
    test, elle était refusée mais imputée à personne."""
    code, sortie = _session_pytest(tmp_path, {
        "test_a_module.py": _JOINDRE_EN_DOUCE + """
import pytest


@pytest.fixture(scope="module")
def demontage_bavard():
    yield
    joindre_en_douce("fil-de-l-entre-deux")


def test_a(demontage_bavard):
    assert True
""",
        "test_b_suivant.py": """
def test_b():
    assert True
""",
    })

    assert code == 1, sortie
    assert "ERROR test_b_suivant.py::test_b" in sortie, sortie
    assert "fil-de-l-entre-deux" in sortie, sortie


def test_une_tentative_apres_le_dernier_test_fait_echouer_la_session(tmp_path):
    """Plus de test à faire rougir : c'est le bilan de session qui échoue."""
    code, sortie = _session_pytest(tmp_path, {
        "test_seul.py": _JOINDRE_EN_DOUCE + """
import pytest


@pytest.fixture(scope="module")
def demontage_bavard():
    yield
    joindre_en_douce("fil-du-dernier-mot")


def test_seul(demontage_bavard):
    assert True
""",
    })

    assert code == 1, sortie
    assert "1 passed" in sortie, sortie
    assert "après le dernier test" in sortie, sortie
    assert "fil-du-dernier-mot" in sortie, sortie

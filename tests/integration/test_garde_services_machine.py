"""Le garde réseau des tests d'intégration sait rougir.

Un garde qui ne peut pas rougir est indiscernable d'un garde vert. Ces tests
vérifient donc qu'une connexion vers chaque service de la machine est REFUSÉE et
CONSIGNÉE, y compris quand elle part d'un thread démon, le cas réel :
`klody-journal-client`, `mem-extractor` et `lb-init` en sont tous.

Chaque test vide la liste des tentatives après l'avoir vérifiée. Sinon le garde
ferait échouer, au démontage, le test qui vérifie justement qu'il fonctionne.
"""
from __future__ import annotations

import socket
import threading
from urllib.parse import urlsplit

import config
import pytest
from agent import journal_client

from tests.integration.conftest import services_de_la_machine

SERVICES = {
    "gateway klody-core": lambda: config.MLX_BASE_URL,
    "journal d'usage": journal_client.gateway_root,
    "Ollama": lambda: config.OLLAMA_BASE_URL,
    "LibraryBrain": lambda: config.LIBRARYBRAIN_URL,
    "MCP Klody": lambda: config.KLODY_MCP_URL,
}


def _port(url: str) -> int:
    return urlsplit(url).port or 80


@pytest.mark.parametrize("nom", sorted(SERVICES))
def test_le_garde_couvre_le_service(nom):
    """Chaque service est dans le périmètre, sous son nom, au port de la config."""
    port = _port(SERVICES[nom]())
    noms, hotes = services_de_la_machine()[port]
    assert nom in noms
    assert {"127.0.0.1", "::1"} <= hotes


@pytest.mark.parametrize("nom", sorted(SERVICES))
def test_une_connexion_depuis_un_thread_demon_est_refusee_et_consignee(
    nom, _services_machine_hors_reseau
):
    tentatives = _services_machine_hors_reseau
    port = _port(SERVICES[nom]())
    erreurs: list[BaseException] = []

    def _joindre() -> None:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        except OSError as exc:
            erreurs.append(exc)

    fil = threading.Thread(target=_joindre, name="demon-du-test", daemon=True)
    fil.start()
    fil.join(timeout=5)

    # Garde absent : la connexion aboutit sur la machine de dev (service
    # vivant) ou est refusée par le système en CI, mais rien n'est consigné.
    # Les deux cas rougissent ici.
    consignees = list(tentatives)
    tentatives.clear()
    assert erreurs and isinstance(erreurs[0], ConnectionRefusedError), erreurs
    assert len(consignees) == 1, consignees
    assert f"127.0.0.1:{port}" in consignees[0]
    assert nom in consignees[0]
    assert "demon-du-test" in consignees[0]


def test_connect_ex_est_couvert_aussi(_services_machine_hors_reseau):
    tentatives = _services_machine_hors_reseau
    port = _port(config.MLX_BASE_URL)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        code = s.connect_ex(("127.0.0.1", port))
    consignees = list(tentatives)
    tentatives.clear()
    assert code != 0
    assert len(consignees) == 1 and f"127.0.0.1:{port}" in consignees[0]


def test_une_connexion_hors_perimetre_passe(_services_machine_hors_reseau):
    """Le garde ne bloque QUE les services nommés, pas tout le réseau."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as serveur:
        serveur.bind(("127.0.0.1", 0))
        serveur.listen(1)
        port = serveur.getsockname()[1]
        assert port not in services_de_la_machine()
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    assert _services_machine_hors_reseau == []

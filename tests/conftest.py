"""Fixtures globales de la suite Klody."""
import logging

import config
import pytest
from agent import semantic_memory


@pytest.fixture(scope="session", autouse=True)
def _pas_de_pollution_du_log_prod():
    """La suite ne DOIT PAS écrire dans le vrai logs/agent.log.

    config.py attache un FileHandler(LOG_FILE) au logger racine dès l'import
    (basicConfig). Sans ce garde-fou, tout test qui exerce VOLONTAIREMENT un
    chemin d'erreur — typiquement tools.voice::TestPannes (« vocalbrain rc=1 :
    Personnage introuvable », « WAV introuvable », « afplay absent ») — écrit des
    lignes WARNING/ERROR dans le journal de PROD. En relecture, ces lignes de test
    passent pour de vraies pannes live (fausse alarme vécue 11/07 : le triplet
    d'erreurs voice était en fait la suite de tests, pas une session réelle).
    On détache les FileHandler le temps de la session, puis on les remet.
    """
    root = logging.getLogger()
    detached = [h for h in list(root.handlers) if isinstance(h, logging.FileHandler)]
    for h in detached:
        root.removeHandler(h)
    yield
    for h in detached:
        root.addHandler(h)


@pytest.fixture(autouse=True)
def _semantic_memory_isolee(monkeypatch, tmp_path):
    """La mémoire sémantique ne touche JAMAIS la base réelle pendant les tests.

    Sans ce garde-fou, tout test qui passe par LongTermMemory.remember() (miroir)
    écrirait dans logs/semantic_memory.db ET chargerait le vrai modèle
    d'embeddings (vécu : 42 entrées de test dans la base de prod). Miroir
    désactivé par défaut ; les tests dédiés (test_semantic_memory) le réactivent
    et configurent explicitement une base tmp_path. La db par défaut est de
    toute façon détournée vers tmp_path (ceinture + bretelles), et l'état
    process du module est remis à zéro entre tests.
    """
    monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", False)
    monkeypatch.setattr(config, "SEMANTIC_MEMORY_DB", tmp_path / "semantic_memory.db")
    yield
    semantic_memory._provider = None
    semantic_memory._configured_db = None

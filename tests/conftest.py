"""Fixtures globales de la suite Klody."""
import config
import pytest
from agent import semantic_memory


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

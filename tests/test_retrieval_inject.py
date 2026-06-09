"""Tests du retrieval proactif (Levier 1c) : Orchestrator._relevant_files_section.

Injecte en PISTES les fichiers sémantiquement proches de la requête. Doit être
best-effort : jamais d'exception qui remonte, et silencieux dès qu'une condition
n'est pas réunie (flag off, query vide, index KO, hits sous le seuil)."""
from types import SimpleNamespace

import pytest


def _hit(rel_path, score):
    return SimpleNamespace(rel_path=rel_path, score=score, preview="…")


def _orch(hits=None, available=True, raises=False):
    from agent.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)

    class _FakeIndex:
        def is_available(self):
            return available

        def search(self, query, k=5):
            if raises:
                raise RuntimeError("embed backend down")
            return (hits or [])[:k]

    o._embed_index = _FakeIndex()  # la property embed_index renvoie ceci (non-None)
    return o


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    """Réglages déterministes, indépendants de l'environnement/.env."""
    monkeypatch.setattr("agent.orchestrator.RETRIEVAL_INJECT_ENABLED", True)
    monkeypatch.setattr("agent.orchestrator.RETRIEVAL_INJECT_K", 5)
    monkeypatch.setattr("agent.orchestrator.RETRIEVAL_MIN_SCORE", 0.35)


class TestRelevantFilesSection:
    def test_injecte_les_hits_au_dessus_du_seuil(self):
        o = _orch([_hit("agent/memory.py", 0.61), _hit("agent/llm.py", 0.42)])
        section = o._relevant_files_section("où est géré le budget de contexte ?")
        assert "Fichiers du projet probablement pertinents" in section
        assert "agent/memory.py" in section
        assert "agent/llm.py" in section

    def test_filtre_les_hits_sous_le_seuil(self):
        o = _orch([_hit("agent/memory.py", 0.61), _hit("README.md", 0.12)])
        section = o._relevant_files_section("budget de contexte")
        assert "agent/memory.py" in section
        assert "README.md" not in section  # 0.12 < 0.35

    def test_query_vide_donne_section_vide(self):
        o = _orch([_hit("a.py", 0.9)])
        assert o._relevant_files_section("   ") == ""

    def test_index_indisponible_donne_section_vide(self):
        o = _orch([_hit("a.py", 0.9)], available=False)
        assert o._relevant_files_section("question") == ""

    def test_flag_off_donne_section_vide(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.RETRIEVAL_INJECT_ENABLED", False)
        o = _orch([_hit("a.py", 0.9)])
        assert o._relevant_files_section("question") == ""

    def test_erreur_recherche_est_silencieuse(self):
        o = _orch(raises=True)
        assert o._relevant_files_section("question") == ""  # pas d'exception

    def test_aucun_hit_au_dessus_du_seuil_donne_vide(self):
        o = _orch([_hit("a.py", 0.20), _hit("b.py", 0.10)])
        assert o._relevant_files_section("bonjour comment vas-tu") == ""

    def test_respecte_k(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.RETRIEVAL_INJECT_K", 2)
        hits = [_hit(f"f{i}.py", 0.9 - i * 0.01) for i in range(10)]
        o = _orch(hits)
        section = o._relevant_files_section("question")
        # search() reçoit k=2 → seuls 2 chemins listés
        assert section.count("- `") == 2

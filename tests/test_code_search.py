"""Tests pour tools/code_search — embeddings + cosine.

Les tests qui dépendent d'Ollama sont marqués @slow et skipés si Ollama down.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from tools.code_search import (
    MISS_EMPTY_INDEX,
    MISS_ENGINE,
    MISS_QUERY_EMBED,
    EmbeddingIndex,
    SearchHit,
    _cosine,
    format_hits,
    format_miss,
)

# ── Cosine ─────────────────────────────────────────────────────────────────────


class TestCosine:
    def test_identique(self):
        assert _cosine([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_oppose(self):
        assert _cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_vecteur_nul(self):
        assert _cosine([0, 0], [1, 1]) == 0.0

    def test_dimensions_differentes(self):
        assert _cosine([1, 0], [1, 0, 0]) == 0.0

    def test_vide(self):
        assert _cosine([], [1, 2]) == 0.0


# ── Formatters ─────────────────────────────────────────────────────────────────


class TestFormatHits:
    def test_vide_nomme_la_panne_et_ne_conclut_pas(self):
        # Audit 27/07 : « Aucun fichier pertinent trouvé. » se lisait comme
        # « ce code n'existe pas ». search() n'a AUCUN seuil de pertinence →
        # zéro hit = panne moteur/index, jamais un verdict sur le code.
        s = format_hits([])
        assert "PANNE" in s
        assert "Ne conclus donc PAS" in s
        assert "search_in_files" in s
        assert "Aucun fichier pertinent" not in s

    def test_avec_resultats(self):
        hits = [
            SearchHit(rel_path="a.py", score=0.9, preview="contenu a"),
            SearchHit(rel_path="b/c.py", score=0.7, preview="contenu c"),
        ]
        s = format_hits(hits)
        assert "0.900" in s
        assert "a.py" in s
        assert "b/c.py" in s


# ── EmbeddingIndex avec embeddings simulés ────────────────────────────────────


class TestEmbeddingIndexMocked:
    def test_unavailable_si_moteur_embeddings_absent(self, tmp_path, monkeypatch):
        # Depuis la migration vers le memory bus (2026-07-18), l'indisponibilité
        # n'est plus un échec réseau mais une dépendance manquante.
        from tools import embeddings

        monkeypatch.setattr(embeddings, "is_available", lambda: False)
        idx = EmbeddingIndex(tmp_path)
        assert idx.is_available() is False
        # refresh ne doit rien faire si indispo
        assert idx.refresh() == 0
        # search retourne vide
        assert idx.search("anything") == []

    def test_moteur_absent_pose_la_cause(self, tmp_path, monkeypatch):
        from tools import embeddings

        monkeypatch.setattr(embeddings, "is_available", lambda: False)
        idx = EmbeddingIndex(tmp_path)
        assert idx.search("anything") == []
        assert idx.last_miss == MISS_ENGINE

    def test_index_vide_pose_la_cause(self, tmp_path, monkeypatch):
        # Moteur VIVANT mais racine sans fichier indexable : l'ancien code
        # tombait sur « Aucun fichier pertinent trouvé » — un verdict, pas une
        # panne. Ce chemin n'était couvert par AUCUN garde (audit 27/07).
        from tools import embeddings

        monkeypatch.setattr(embeddings, "is_available", lambda: True)
        idx = EmbeddingIndex(tmp_path)  # tmp_path vide
        assert idx.search("anything") == []
        assert idx.last_miss == MISS_EMPTY_INDEX

    def test_embed_de_requete_vide_pose_la_cause(self, tmp_path, monkeypatch):
        # Cas le plus vicieux : index peuplé, moteur « available », mais
        # l'embedding de la requête rend vide (bge-m3 muet, cf. incident
        # nightly vec-down). Zéro hit → panne, surtout pas « rien de pertinent ».
        from tools import embeddings

        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        calls = {"n": 0}

        def fake_batch(texts):
            calls["n"] += 1
            # 1er appel = indexation des fichiers (OK), 2e = requête (KO).
            return [] if calls["n"] > 1 else [[1.0, 0.0, 0.0] for _ in texts]

        monkeypatch.setattr(embeddings, "is_available", lambda: True)
        monkeypatch.setattr(embeddings, "embed_batch", fake_batch)

        idx = EmbeddingIndex(tmp_path)
        assert idx.refresh() == 1
        assert idx.search("anything") == []
        assert idx.last_miss == MISS_QUERY_EMBED

    def test_last_miss_remis_a_zero_quand_la_recherche_rend_des_hits(
        self, tmp_path, monkeypatch
    ):
        from tools import embeddings

        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        monkeypatch.setattr(embeddings, "is_available", lambda: True)
        monkeypatch.setattr(
            embeddings, "embed_batch", lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
        )
        idx = EmbeddingIndex(tmp_path)
        idx.last_miss = MISS_ENGINE  # résidu d'une recherche précédente
        assert idx.search("anything", k=1)
        assert idx.last_miss is None

    def test_format_miss_nomme_chaque_cause(self):
        for reason in (MISS_ENGINE, MISS_EMPTY_INDEX, MISS_QUERY_EMBED):
            msg = format_miss(reason)
            assert reason in msg
            assert "PANNE" in msg
            assert "Ne conclus donc PAS" in msg

    def test_format_miss_sans_cause_reste_prudent(self):
        msg = format_miss(None)
        assert "cause inconnue" in msg
        assert "Ne conclus donc PAS" in msg

    def test_skip_venv_et_node_modules(self, tmp_path):
        (tmp_path / "ok.py").write_text("print('hello')", encoding="utf-8")
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "fake.py").write_text("x = 1", encoding="utf-8")
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}", encoding="utf-8")
        idx = EmbeddingIndex(tmp_path)
        files = list(idx._iter_source_files())
        names = {f.name for f in files}
        assert "ok.py" in names
        assert "fake.py" not in names
        assert "index.js" not in names

    def test_search_via_mock(self, tmp_path, monkeypatch):
        """Flow end-to-end avec un moteur d'embeddings simulé.

        Le mock porte sur `tools.embeddings` (la couture depuis 2026-07-18). Sans
        lui le test calculerait de VRAIS vecteurs bge-m3 : lent, et il ne testerait
        plus le flow mais le modèle.
        """
        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def bar(): pass\n", encoding="utf-8")

        from tools import embeddings

        def fake_batch(texts):
            # Vecteur déterministe dépendant du contenu.
            return [[(len(t) % 7) / 10.0, 1.0 - (len(t) % 7) / 10.0, 0.5] for t in texts]

        monkeypatch.setattr(embeddings, "is_available", lambda: True)
        monkeypatch.setattr(embeddings, "embed_batch", fake_batch)

        idx = EmbeddingIndex(tmp_path)
        n = idx.refresh()
        assert n == 2
        hits = idx.search("query", k=2)
        assert len(hits) == 2
        # Scores entre -1 et 1 (tolérance FP)
        for h in hits:
            assert -1.001 <= h.score <= 1.001
        # Triés décroissant
        assert hits[0].score >= hits[1].score

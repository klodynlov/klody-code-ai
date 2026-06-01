"""Tests pour tools/code_search — embeddings + cosine.

Les tests qui dépendent d'Ollama sont marqués @slow et skipés si Ollama down.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from tools.code_search import (
    EmbeddingIndex,
    SearchHit,
    _cosine,
    format_hits,
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
    def test_vide(self):
        assert format_hits([]) == "Aucun fichier pertinent trouvé."

    def test_avec_resultats(self):
        hits = [
            SearchHit(rel_path="a.py", score=0.9, preview="contenu a"),
            SearchHit(rel_path="b/c.py", score=0.7, preview="contenu c"),
        ]
        s = format_hits(hits)
        assert "0.900" in s
        assert "a.py" in s
        assert "b/c.py" in s


# ── EmbeddingIndex avec mock Ollama ────────────────────────────────────────────


class TestEmbeddingIndexMocked:
    def test_unavailable_si_ollama_down(self, tmp_path, monkeypatch):
        # Force is_available à False via une exception HTTP
        def raise_(*a, **k):
            raise httpx.ConnectError("connection refused")
        monkeypatch.setattr(httpx, "get", raise_)
        idx = EmbeddingIndex(tmp_path)
        assert idx.is_available() is False
        # refresh ne doit rien faire si indispo
        assert idx.refresh() == 0
        # search retourne vide
        assert idx.search("anything") == []

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
        """Mock /api/tags + /api/embed pour tester le flow end-to-end."""
        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def bar(): pass\n", encoding="utf-8")

        def fake_get(url, **kw):
            req = httpx.Request("GET", url)
            return httpx.Response(200, json={"models": [{"name": "bge-m3"}]}, request=req)

        # Counter pour donner des embeddings différents par appel
        call = {"n": 0}

        def fake_post(url, json=None, **kw):
            call["n"] += 1
            # 1er + 2e appel : batch d'index (2 fichiers)
            # 3e appel : query
            inputs = (json or {}).get("input", [])
            # Vecteur dépendant du contenu : longueur + 1er char
            vecs = []
            for txt in inputs:
                seed = (len(txt) % 7) / 10.0
                vecs.append([seed, 1.0 - seed, 0.5])
            req = httpx.Request("POST", url)
            return httpx.Response(200, json={"embeddings": vecs}, request=req)

        monkeypatch.setattr(httpx, "get", fake_get)
        monkeypatch.setattr(httpx, "post", fake_post)

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

"""Tests de tools.library_distiller — distillation d'un thème multi-livres.

DB SQLite jetable au schéma réel Library Brain (books + chunks + chunks_fts
external-content), LLM factice : aucun réseau, aucun accès à la vraie DB.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tools.library_distiller as ld
from tools.library_distiller import (
    _digest_slug,
    _parse_llm_json,
    _terms,
    distill_theme,
    harvest,
    rank_books,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_db(path: Path, with_fts: bool = True) -> None:
    """DB minimale au schéma Library Brain : 2 livres, 1 thème discriminant."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, author TEXT,
                            category TEXT, format TEXT, file_path TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, book_id INTEGER,
                             chunk_index INTEGER, text TEXT, page INTEGER,
                             chapter TEXT, token_count INTEGER);
        """
    )
    con.execute("INSERT INTO books VALUES (1, 'Maîtriser WebGL', 'A. Dupont', 'tech', 'pdf', '/x.pdf')")
    con.execute("INSERT INTO books VALUES (2, 'Cuisine italienne', 'B. Rossi', 'cuisine', 'epub', '/y.epub')")
    rows = [
        (1, 1, 0, "Le pipeline webgl utilise des shaders pour le rendu.", 12, "ch1"),
        (2, 1, 1, "Optimiser webgl exige de mesurer avant de toucher aux shaders.", 45, "ch3"),
        (3, 1, 2, "Un shader fragment calcule la couleur de chaque pixel.", 46, "ch3"),
        (4, 2, 0, "La pasta se sale à l'eau de cuisson.", 7, "ch1"),
    ]
    con.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?, 10)", rows)
    if with_fts:
        con.executescript(
            """
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                text, content='chunks', content_rowid='id');
            INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild');
            """
        )
    con.commit()
    con.close()


@pytest.fixture
def db(tmp_path, monkeypatch) -> Path:
    p = tmp_path / "library.db"
    _make_db(p)
    monkeypatch.setattr(ld, "LIBRARY_DB_PATH", p)
    return p


@pytest.fixture
def skills_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "skills"
    monkeypatch.setattr(ld, "SKILLS_DIR", d)
    return d


class _FakeLLM:
    """stream_chat factice : rejoue une (ou plusieurs) réponse(s) canée(s)."""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls = 0
        self.captured: list[dict] | None = None

    def stream_chat(self, messages, **kwargs):
        self.captured = messages
        resp = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return resp, None


_GOOD_JSON = json.dumps({
    "name": "Digest WebGL (Library Brain)",
    "description": "webgl, shaders, rendu 3D, optimisation, pixel, jeu, visualisation",
    "content": "RÈGLES CRITIQUES : mesurer avant d'optimiser, d'après Maîtriser WebGL.",
})


# ── _terms / slug / parse ────────────────────────────────────────────────────


class TestHelpers:
    def test_terms_filtre_stopwords_et_courts(self):
        assert _terms("comment faire de l'optimisation WebGL") == ["optimisation", "webgl"]

    def test_terms_barriere_injection_fts(self):
        # Guillemets/opérateurs FTS jamais transmis : alphanumérique strict.
        for t in _terms('webgl" OR x NEAR(y) AND "shaders'):
            assert t.isalnum()

    def test_digest_slug_prefixe_et_borne(self):
        assert _digest_slug("Modélisation 3D avec three.js") == "digest_modelisation_3d_avec_three_js"
        assert len(_digest_slug("x" * 100)) <= 40

    def test_digest_slug_jamais_gate_distiller(self):
        # `distiller*` est gaté par le routeur → le préfixe est purgé.
        assert _digest_slug("distiller un livre").startswith("digest_")
        assert not _digest_slug("distiller un livre").startswith("digest_distiller")

    def test_digest_slug_pas_de_double_prefixe(self):
        assert _digest_slug("digest_webgl") == "digest_webgl"

    def test_parse_llm_json_bloc_markdown(self):
        text = f"Voici :\n```json\n{_GOOD_JSON}\n```\nfini."
        assert _parse_llm_json(text)["name"].startswith("Digest")

    def test_parse_llm_json_nu(self):
        assert _parse_llm_json(_GOOD_JSON)["description"]

    def test_parse_llm_json_champ_manquant(self):
        with pytest.raises(ValueError, match="champs manquants"):
            _parse_llm_json('{"name": "x"}')


# ── rank / harvest (DB réelle jetable) ───────────────────────────────────────


class TestRankHarvest:
    def test_rank_trouve_le_bon_livre(self, db):
        books = rank_books("optimisation webgl shaders")
        assert books and books[0]["id"] == 1
        # 2 chunks : « webgl »/« shaders » exacts. Le 3ᵉ dit « shader » au
        # singulier — FTS5 ne stemme pas, il ne matche pas. Comportement assumé.
        assert books[0]["hits"] == 2
        assert all(b["id"] != 2 for b in books)  # la cuisine ne matche pas

    def test_rank_theme_vide(self, db):
        assert rank_books("de la et") == []

    def test_rank_and_filtre_le_bruit(self, db, tmp_path, monkeypatch):
        # Un chunk cuisine qui dit « webgl » SANS « shaders » : la passe AND
        # (tous les termes dans le même chunk) l'écarte — c'est elle qui évite
        # qu'un terme fréquent isolé fasse gagner un livre hors sujet.
        con = sqlite3.connect(db)
        con.execute("PRAGMA query_only=0")
        con.execute("INSERT INTO chunks VALUES (5, 2, 1, 'un webgl dans la cuisine', 9, 'ch2', 10)")
        con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        con.commit(); con.close()
        books = rank_books("webgl shaders")
        assert [b["id"] for b in books] == [1]

    def test_harvest_rapporte_pages(self, db):
        books = rank_books("webgl")
        ex = harvest(books, "webgl")
        assert ex and {e["page"] for e in ex} <= {12, 45, 46}

    def test_fallback_like_sans_fts(self, tmp_path, monkeypatch):
        # DB sans table FTS (index cassé/absent) → repli LIKE silencieux.
        p = tmp_path / "nofts.db"
        _make_db(p, with_fts=False)
        monkeypatch.setattr(ld, "LIBRARY_DB_PATH", p)
        books = rank_books("webgl shaders")
        assert books and books[0]["id"] == 1
        assert harvest(books, "webgl")

    def test_db_absente_message_clair(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ld, "LIBRARY_DB_PATH", tmp_path / "absente.db")
        with pytest.raises(FileNotFoundError, match="LIBRARY_DB_PATH"):
            rank_books("webgl")


# ── distill_theme (bout en bout, LLM factice) ────────────────────────────────


class TestDistillTheme:
    def test_ecrit_le_digest_couche_a(self, db, skills_dir):
        llm = _FakeLLM(_GOOD_JSON)
        out = distill_theme("optimisation webgl", llm=llm)
        assert "✅" in out
        path = skills_dir / "digest_optimisation_webgl.json"
        data = json.loads(path.read_text())
        assert data["slug"] == "digest_optimisation_webgl"
        assert "code_compatible" not in data  # défaut : brain seul

    def test_code_compatible_tague(self, db, skills_dir):
        distill_theme("webgl", code_compatible=True, llm=_FakeLLM(_GOOD_JSON))
        data = json.loads((skills_dir / "digest_webgl.json").read_text())
        assert data["code_compatible"] is True

    def test_corpus_dans_le_prompt(self, db, skills_dir):
        llm = _FakeLLM(_GOOD_JSON)
        distill_theme("webgl", llm=llm)
        system = llm.captured[0]["content"]
        assert "Maîtriser WebGL" in system  # extraits réellement injectés
        assert "{{corpus}}" not in system   # substitution faite

    def test_theme_sans_livre(self, db, skills_dir):
        out = distill_theme("trombone baroque", llm=_FakeLLM(_GOOD_JSON))
        assert "Aucun livre" in out

    def test_synthese_illisible_apres_2_tentatives(self, db, skills_dir):
        llm = _FakeLLM("pas du json")
        out = distill_theme("webgl", llm=llm)
        assert "Synthèse illisible après 2 tentatives" in out
        assert llm.calls == 2
        assert not list(skills_dir.glob("*.json"))  # rien d'écrit

    def test_retry_rattrape_un_rate(self, db, skills_dir):
        # 1ʳᵉ génération vide (raté constaté en réel), la 2ᵉ passe.
        llm = _FakeLLM("", _GOOD_JSON)
        out = distill_theme("webgl", llm=llm)
        assert "✅" in out and llm.calls == 2

    def test_sans_llm(self):
        assert "client LLM" in distill_theme("webgl")

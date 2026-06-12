"""Tests de agent/semantic_memory.py — archive sémantique (klody_memory, 3ᵉ consommateur).

Hermétiques : les embeddings sont REMPLACÉS par des vecteurs déterministes
(bag-of-words sur un petit vocabulaire) — aucun modèle réel chargé, aucune
dépendance réseau/daemon. La jambe FTS5 et toute la mécanique SQL (insert,
replace, forget, filtre kind, miroir LongTermMemory) sont, elles, réelles.
La validation avec le vrai bge-m3 se fait via le script de backfill (smoke live).
"""
import math
import sqlite3

import config
import pytest
from agent import semantic_memory

pytestmark = pytest.mark.skipif(
    not semantic_memory.MEMORY_AVAILABLE,
    reason="paquet klody-memory (ou ses deps) non installé",
)

_VOCAB = ["python", "fastapi", "tauri", "react", "gateway", "mlx", "piano", "musique"]


def _fake_vec(text, *_a, **_kw):
    t = (text or "").lower()
    # 0.1 de base : jamais de vecteur nul (texte hors vocabulaire), norme définie.
    v = [1.0 if w in t else 0.1 for w in _VOCAB]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """Mémoire isolée dans tmp_path, embeddings déterministes. Yield le chemin db.
    Ré-active le flag coupé par le conftest (autouse) — base tmp explicite ici."""
    monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
    monkeypatch.setattr(
        "klody_memory.embedder.get_embeddings_batch",
        lambda texts, *a, **kw: [_fake_vec(t) for t in texts],
    )
    monkeypatch.setattr("klody_memory.embedder.get_embedding", _fake_vec)
    monkeypatch.setattr("klody_memory.retriever.get_embedding", _fake_vec)
    db = tmp_path / "semantic.db"
    semantic_memory.configure_memory(db_path=db)
    yield db
    semantic_memory.reset_memory(db)


def _count_books(db, title=None):
    conn = sqlite3.connect(str(db))
    try:
        if title is None:
            return conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM books WHERE title = ?", (title,)
        ).fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------------ #
# remember / recall                                                    #
# ------------------------------------------------------------------ #

class TestRememberRecall:
    def test_rappel_semantique(self, mem):
        semantic_memory.remember(
            "Préfère Python et FastAPI pour le backend",
            title="stack_backend", kind="preference",
        )
        semantic_memory.remember(
            "L'UI de Klody est en React + Tauri",
            title="stack_ui", kind="project",
        )
        hits = semantic_memory.recall("interface react tauri", top_k=1)
        assert hits
        assert hits[0].title == "stack_ui"
        assert hits[0].kind == "project"

    def test_filtre_kind(self, mem):
        semantic_memory.remember("Python FastAPI", title="a", kind="preference")
        semantic_memory.remember("React Tauri", title="b", kind="project")
        hits = semantic_memory.recall("python react", top_k=5, kind="preference")
        assert hits
        assert all(h.kind == "preference" for h in hits)

    def test_replace_pas_de_doublon(self, mem):
        id1 = semantic_memory.remember("v1 : gateway MLX", title="fait", replace=True)
        id2 = semantic_memory.remember("v2 : gateway React", title="fait", replace=True)
        assert id2 != id1
        assert _count_books(mem, "fait") == 1
        hits = semantic_memory.recall("gateway", top_k=3)
        textes = [h.text for h in hits if h.title == "fait"]
        assert textes == ["v2 : gateway React"]

    def test_sans_replace_duplique(self, mem):
        semantic_memory.remember("v1 gateway", title="fait")
        semantic_memory.remember("v2 gateway", title="fait")
        assert _count_books(mem, "fait") == 2

    def test_texte_vide_refuse(self, mem):
        with pytest.raises(ValueError):
            semantic_memory.remember("   ", title="x")
        with pytest.raises(ValueError):
            semantic_memory.remember("contenu", title=" ")


# ------------------------------------------------------------------ #
# forget                                                               #
# ------------------------------------------------------------------ #

class TestForget:
    def test_forget_supprime_tout(self, mem):
        semantic_memory.remember("fait sur le gateway mlx", title="fait")
        assert semantic_memory.forget("fait") == 1
        assert _count_books(mem, "fait") == 0
        assert not [h for h in semantic_memory.recall("gateway mlx") if h.title == "fait"]

    def test_forget_inconnu(self, mem):
        assert semantic_memory.forget("inexistant") == 0

    def test_forget_filtre_kind(self, mem):
        semantic_memory.remember("python", title="x", kind="preference")
        semantic_memory.remember("react", title="x", kind="project")
        assert semantic_memory.forget("x", kind="project") == 1
        assert _count_books(mem, "x") == 1


# ------------------------------------------------------------------ #
# recall_for_llm (outil rappeler_memoire)                              #
# ------------------------------------------------------------------ #

class TestRecallForLLM:
    def test_format(self, mem):
        semantic_memory.remember("L'UI est en React Tauri", title="stack_ui", kind="project")
        out = semantic_memory.recall_for_llm("react tauri", top_k=3)
        assert "stack_ui" in out
        assert "[project]" in out

    def test_base_vide(self, mem):
        out = semantic_memory.recall_for_llm("n'importe quoi")
        assert "Aucun souvenir" in out

    def test_indisponible_message_clair(self, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
        monkeypatch.setattr(semantic_memory, "MEMORY_AVAILABLE", False)
        out = semantic_memory.recall_for_llm("test")
        assert "indisponible" in out

    def test_desactive_message_clair(self, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", False)
        out = semantic_memory.recall_for_llm("test")
        assert "désactivée" in out


# ------------------------------------------------------------------ #
# Miroir LongTermMemory → mémoire sémantique                           #
# ------------------------------------------------------------------ #

@pytest.fixture
def lt(tmp_path, monkeypatch):
    """LongTermMemory isolée dans tmp_path (même pattern que test_long_term_memory)."""
    from agent.long_term_memory import LongTermMemory
    monkeypatch.setattr("agent.long_term_memory._STORAGE", tmp_path / "long_term.json")
    monkeypatch.setattr("agent.long_term_memory._instance", None)
    return LongTermMemory()


class TestMiroirLongTerm:
    def test_remember_fact_miroite(self, lt, mem):
        lt.remember("stack_ui", "React et Tauri pour l'UI", "project")
        hits = semantic_memory.recall("react tauri", top_k=3)
        assert any(h.title == "stack_ui" and h.kind == "project" for h in hits)

    def test_update_sans_doublon(self, lt, mem):
        lt.remember("langage", "Python", "preference")
        lt.remember("langage", "Python et FastAPI", "preference")
        assert _count_books(mem, "langage") == 1
        hits = semantic_memory.recall("python fastapi", top_k=3)
        textes = [h.text for h in hits if h.title == "langage"]
        assert textes == ["Python et FastAPI"]

    def test_forget_miroite(self, lt, mem):
        lt.remember("langage", "Python", "preference")
        lt.forget("langage")
        assert _count_books(mem, "langage") == 0

    def test_echec_miroir_silencieux(self, lt, mem, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("base en vrac")
        monkeypatch.setattr(semantic_memory, "remember", boom)
        out = lt.remember("cle", "contenu python")
        assert out.startswith("Mémorisé")
        assert len(lt.entries) == 1   # le chemin principal n'est jamais impacté

    def test_desactive_pas_de_miroir(self, lt, mem, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", False)
        lt.remember("cle", "contenu python")
        assert _count_books(mem) == 0

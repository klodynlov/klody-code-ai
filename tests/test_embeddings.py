"""Tests de tools/embeddings.py — façade d'embeddings partagée.

Même cause que agent/semantic_memory.py avant #167 : le module délègue à
`klody_memory`, absent en CI, donc rien de sa logique n'était exécuté (18 % de
couverture). On réutilise le double de tests/fake_klody_memory.py.

Ce qui est jugé ici est propre à ce module : l'**emprunt** de configuration au
propriétaire (`agent.semantic_memory`) plutôt qu'un second `configure()` qui
écraserait la connexion de la mémoire sémantique, le cache de disponibilité, et
l'alignement systématique de la sortie sur l'entrée.
"""
from __future__ import annotations

import importlib

import pytest

from tests import fake_klody_memory


@pytest.fixture
def emb(tmp_path, monkeypatch):
    """Double injecté + `tools.embeddings` et son propriétaire rechargés."""
    import config

    fake_klody_memory.reset_state()
    evicted = fake_klody_memory.install()

    from agent import semantic_memory
    from tools import embeddings

    importlib.reload(semantic_memory)
    monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "SEMANTIC_MEMORY_DB", tmp_path / "emb.db")
    embeddings._reset_cache()

    try:
        yield embeddings
    finally:
        embeddings._reset_cache()
        semantic_memory.reset_memory(tmp_path / "emb.db")
        fake_klody_memory.uninstall(evicted)
        fake_klody_memory.reset_state()
        importlib.reload(semantic_memory)


# --- disponibilité ---------------------------------------------------------


def test_is_available_configure_le_proprietaire(emb, tmp_path):
    """Le module ne doit PAS appeler klody_memory.configure() lui-même : il passe
    par agent.semantic_memory, seul propriétaire des singletons. Un second
    configure() écraserait la connexion de la mémoire sémantique."""
    from agent import semantic_memory

    assert semantic_memory.is_ready() is False
    assert emb.is_available() is True
    assert semantic_memory.is_ready() is True
    assert (tmp_path / "emb.db").exists()


def test_is_available_reutilise_une_configuration_existante(emb, tmp_path):
    """Si le propriétaire est déjà branché, on ne reconfigure pas — sinon chaque
    embed re-ciblerait la base et réinitialiserait les mémos du moteur."""
    from agent import semantic_memory

    autre = tmp_path / "deja.db"
    semantic_memory.configure_memory(db_path=autre)
    emb._reset_cache()

    assert emb.is_available() is True
    assert semantic_memory._configured_db == autre


def test_is_available_est_mis_en_cache(emb, monkeypatch):
    """Le premier appel décide, les suivants ne retouchent pas le propriétaire."""
    assert emb.is_available() is True

    appels = []
    monkeypatch.setattr(emb, "_ensure_ready", lambda: appels.append(1) or True)
    assert emb.is_available() is True
    assert appels == []


def test_is_available_faux_si_moteur_absent(emb, monkeypatch):
    from agent import semantic_memory

    monkeypatch.setattr(semantic_memory, "MEMORY_AVAILABLE", False)
    emb._reset_cache()

    assert emb.is_available() is False


def test_is_available_avale_une_exception(emb, monkeypatch):
    """Contrat de dégradation douce : indisponible, jamais une levée."""
    from agent import semantic_memory

    def boom(*a, **kw):
        raise RuntimeError("base corrompue")

    monkeypatch.setattr(semantic_memory, "configure_memory", boom)
    emb._reset_cache()

    assert emb.is_available() is False


def test_reset_cache_rearme_la_detection(emb, monkeypatch):
    from agent import semantic_memory

    assert emb.is_available() is True
    monkeypatch.setattr(semantic_memory, "MEMORY_AVAILABLE", False)

    assert emb.is_available() is True   # encore en cache
    emb._reset_cache()
    assert emb.is_available() is False  # re-testé


# --- embed_batch -----------------------------------------------------------


def test_embed_batch_rend_un_vecteur_par_texte(emb):
    vecs = emb.embed_batch(["python fastapi", "react tauri"])

    assert len(vecs) == 2
    assert all(len(v) > 0 for v in vecs)
    assert vecs[0] != vecs[1]


def test_embed_batch_liste_vide(emb):
    assert emb.embed_batch([]) == []


def test_embed_batch_aligne_la_sortie_sur_l_entree_si_indisponible(emb, monkeypatch):
    """Contrat repris de l'ancien client HTTP : les appelants indexent par
    position (code_search, skill_router). Une liste plus courte les décalerait
    silencieusement sur le mauvais document."""
    monkeypatch.setattr(emb, "is_available", lambda: False)
    assert emb.embed_batch(["a", "b", "c"]) == [[], [], []]


def test_embed_batch_aligne_aussi_en_cas_d_echec(emb, monkeypatch):
    import sys

    def boom(*a, **kw):
        raise RuntimeError("modèle déchargé")

    monkeypatch.setattr(sys.modules["klody_memory.embedder"], "get_embeddings_batch", boom)
    assert emb.embed_batch(["a", "b"]) == [[], []]


def test_embed_batch_traduit_none_en_liste_vide(emb):
    """Le moteur rend None pour un texte échoué ; l'appelant attend []."""
    vecs = emb.embed_batch(["python", "   "])

    assert len(vecs) == 2
    assert vecs[0] and vecs[1] == []


# --- embed_one -------------------------------------------------------------


def test_embed_one_delegue_au_batch(emb):
    seul = emb.embed_one("python fastapi")
    lot = emb.embed_batch(["python fastapi"])

    assert seul == lot[0]


@pytest.mark.parametrize("texte", ["", "   ", "\n\t"])
def test_embed_one_court_circuite_le_vide(emb, texte, monkeypatch):
    """Un texte vide ne doit pas atteindre le moteur du tout."""
    monkeypatch.setattr(emb, "embed_batch", lambda t: pytest.fail("ne doit pas être appelé"))
    assert emb.embed_one(texte) == []


def test_embed_one_rend_vide_si_indisponible(emb, monkeypatch):
    monkeypatch.setattr(emb, "is_available", lambda: False)
    assert emb.embed_one("python") == []

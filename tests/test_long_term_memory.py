"""Tests de agent/long_term_memory.py — mémoire inter-sessions."""

import json
from pathlib import Path

import pytest
from agent.long_term_memory import LongTermMemory


@pytest.fixture
def lt(tmp_path, monkeypatch):
    """LongTermMemory isolée dans tmp_path."""
    storage = tmp_path / "long_term.json"
    monkeypatch.setattr("agent.long_term_memory._STORAGE", storage)
    # Réinitialiser le singleton
    monkeypatch.setattr("agent.long_term_memory._instance", None)
    m = LongTermMemory()
    return m


# ------------------------------------------------------------------ #
# remember                                                             #
# ------------------------------------------------------------------ #

class TestRemember:
    def test_ajoute_entree(self, lt):
        lt.remember("langage", "Python", "preference")
        assert len(lt.entries) == 1
        assert lt.entries[0]["key"] == "langage"
        assert lt.entries[0]["content"] == "Python"
        assert lt.entries[0]["category"] == "preference"

    def test_cle_normalisee_snake_case(self, lt):
        lt.remember("Mon Projet", "Klody AI", "project")
        assert lt.entries[0]["key"] == "mon_projet"

    def test_mise_a_jour_si_cle_existe(self, lt):
        lt.remember("langage", "Python", "preference")
        lt.remember("langage", "TypeScript", "preference")
        assert len(lt.entries) == 1
        assert lt.entries[0]["content"] == "TypeScript"

    def test_categorie_par_defaut_context(self, lt):
        lt.remember("note", "contenu")
        assert lt.entries[0]["category"] == "context"

    def test_updated_at_present(self, lt):
        lt.remember("k", "v")
        assert lt.entries[0]["updated_at"]

    def test_retourne_message_confirmation(self, lt):
        msg = lt.remember("k", "v", "user")
        assert "user" in msg.lower() or "mémorisé" in msg.lower()

    def test_retourne_mise_a_jour_si_existant(self, lt):
        lt.remember("k", "v1")
        msg = lt.remember("k", "v2")
        assert "mis" in msg.lower() or "jour" in msg.lower()

    def test_key_vide_retourne_erreur(self, lt):
        msg = lt.remember("", "contenu")
        assert "ERREUR" in msg

    def test_content_vide_retourne_erreur(self, lt):
        msg = lt.remember("key", "")
        assert "ERREUR" in msg

    def test_plusieurs_entrees_differentes_cles(self, lt):
        lt.remember("a", "valeur_a", "user")
        lt.remember("b", "valeur_b", "project")
        lt.remember("c", "valeur_c", "preference")
        assert len(lt.entries) == 3


# ------------------------------------------------------------------ #
# forget                                                               #
# ------------------------------------------------------------------ #

class TestForget:
    def test_supprime_entree_existante(self, lt):
        lt.remember("k", "v")
        msg = lt.forget("k")
        assert len(lt.entries) == 0
        assert "oublié" in msg.lower()

    def test_cle_inexistante_retourne_message(self, lt):
        msg = lt.forget("inconnu")
        assert "introuvable" in msg.lower()

    def test_ne_supprime_pas_autre_cle(self, lt):
        lt.remember("a", "va")
        lt.remember("b", "vb")
        lt.forget("a")
        assert len(lt.entries) == 1
        assert lt.entries[0]["key"] == "b"

    def test_cle_normalisee_avant_suppression(self, lt):
        lt.remember("mon_projet", "Klody")
        msg = lt.forget("MON PROJET")
        assert len(lt.entries) == 0
        assert "oublié" in msg.lower()


# ------------------------------------------------------------------ #
# list_all                                                             #
# ------------------------------------------------------------------ #

class TestListAll:
    def test_liste_vide(self, lt):
        assert lt.list_all() == []

    def test_liste_triee_par_categorie_et_cle(self, lt):
        lt.remember("z_key", "z", "user")
        lt.remember("a_key", "a", "user")
        lt.remember("m_key", "m", "project")
        result = lt.list_all()
        # project avant user dans l'ordre alphabétique des catégories
        cats = [e["category"] for e in result]
        assert cats == sorted(cats)

    def test_retourne_toutes_les_entrees(self, lt):
        for i in range(5):
            lt.remember(f"key_{i}", f"val_{i}")
        assert len(lt.list_all()) == 5


# ------------------------------------------------------------------ #
# format_for_prompt                                                    #
# ------------------------------------------------------------------ #

class TestFormatForPrompt:
    def test_vide_retourne_chaine_vide(self, lt):
        assert lt.format_for_prompt() == ""

    def test_contient_section_memoire(self, lt):
        lt.remember("langage", "Python", "preference")
        prompt = lt.format_for_prompt()
        assert "Mémoire longue terme" in prompt
        assert "langage" in prompt
        assert "Python" in prompt

    def test_contient_label_categorie(self, lt):
        lt.remember("projet", "Klody", "project")
        prompt = lt.format_for_prompt()
        assert "Projets en cours" in prompt

    def test_toutes_categories_presentes(self, lt):
        lt.remember("ku", "vu", "user")
        lt.remember("kp", "vp", "project")
        lt.remember("kpr", "vpr", "preference")
        lt.remember("kc", "vc", "context")
        prompt = lt.format_for_prompt()
        assert "Utilisateur" in prompt
        assert "Projets en cours" in prompt
        assert "Préférences" in prompt
        assert "Contexte général" in prompt

    def test_categories_absentes_non_affichees(self, lt):
        lt.remember("k", "v", "user")
        prompt = lt.format_for_prompt()
        assert "Projets en cours" not in prompt

    def test_contient_instructions_outils(self, lt):
        lt.remember("k", "v")
        prompt = lt.format_for_prompt()
        assert "remember_fact" in prompt
        assert "forget_fact" in prompt


# ------------------------------------------------------------------ #
# Persistance                                                          #
# ------------------------------------------------------------------ #

class TestPersistance:
    def test_save_cree_fichier(self, lt, tmp_path):
        storage = tmp_path / "long_term.json"
        lt.remember("k", "v")
        assert storage.exists()

    def test_save_contenu_json_valide(self, lt, tmp_path):
        storage = tmp_path / "long_term.json"
        lt.remember("k", "v", "user")
        data = json.loads(storage.read_text())
        assert isinstance(data, list)
        assert data[0]["key"] == "k"

    def test_load_depuis_fichier_existant(self, tmp_path, monkeypatch):
        storage = tmp_path / "long_term.json"
        storage.write_text(json.dumps([
            {"key": "loaded_key", "content": "loaded_val",
             "category": "context", "updated_at": "2026-01-01T00:00:00"}
        ]))
        monkeypatch.setattr("agent.long_term_memory._STORAGE", storage)
        monkeypatch.setattr("agent.long_term_memory._instance", None)
        lt2 = LongTermMemory()
        assert len(lt2.entries) == 1
        assert lt2.entries[0]["key"] == "loaded_key"

    def test_load_fichier_corrompu_retourne_vide(self, tmp_path, monkeypatch):
        storage = tmp_path / "long_term.json"
        storage.write_text("not valid json {{")
        monkeypatch.setattr("agent.long_term_memory._STORAGE", storage)
        monkeypatch.setattr("agent.long_term_memory._instance", None)
        lt2 = LongTermMemory()
        assert lt2.entries == []

    def test_persistance_apres_forget(self, lt, tmp_path):
        storage = tmp_path / "long_term.json"
        lt.remember("a", "va")
        lt.remember("b", "vb")
        lt.forget("a")
        data = json.loads(storage.read_text())
        assert len(data) == 1
        assert data[0]["key"] == "b"


# ------------------------------------------------------------------ #
# Titre auto dans ConversationMemory                                   #
# ------------------------------------------------------------------ #

class TestConversationMemoryTitle:
    def test_titre_genere_depuis_premier_message_user(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.memory.MEMORY_DIR", tmp_path)
        from agent.memory import ConversationMemory
        m = ConversationMemory(session_id="titretest")
        m.memory_file = tmp_path / "memory_titretest.json"
        m.add_message("user", "Bonjour Klody, comment vas-tu ?")
        assert m.title != ""
        assert "Bonjour" in m.title

    def test_titre_contient_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.memory.MEMORY_DIR", tmp_path)
        from agent.memory import ConversationMemory
        m = ConversationMemory(session_id="datetest")
        m.memory_file = tmp_path / "memory_datetest.json"
        m.add_message("user", "Test")
        # Format attendu : "DD/MM HH:MM — ..."
        assert "/" in m.title and "—" in m.title

    def test_titre_tronque_a_50_chars(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.memory.MEMORY_DIR", tmp_path)
        from agent.memory import ConversationMemory
        m = ConversationMemory(session_id="trunctest")
        m.memory_file = tmp_path / "memory_trunctest.json"
        long_msg = "A" * 100
        m.add_message("user", long_msg)
        # La question dans le titre doit être tronquée (+ préfixe date)
        question_part = m.title.split("—", 1)[-1].strip()
        assert len(question_part) <= 53  # 50 + "…"

    def test_titre_non_ecrase_si_deja_defini(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.memory.MEMORY_DIR", tmp_path)
        from agent.memory import ConversationMemory
        m = ConversationMemory(session_id="keeptest")
        m.memory_file = tmp_path / "memory_keeptest.json"
        m.add_message("user", "Premier message")
        titre_initial = m.title
        m.add_message("user", "Deuxième message")
        assert m.title == titre_initial

    def test_titre_persiste_dans_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.memory.MEMORY_DIR", tmp_path)
        from agent.memory import ConversationMemory
        m = ConversationMemory(session_id="persisttitle")
        m.memory_file = tmp_path / "memory_persisttitle.json"
        m.add_message("user", "Question persistée")
        data = json.loads(m.memory_file.read_text())
        assert "title" in data
        assert data["title"] == m.title

    def test_titre_charge_depuis_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.memory.MEMORY_DIR", tmp_path)
        from agent.memory import ConversationMemory
        m = ConversationMemory(session_id="loadtitle")
        m.memory_file = tmp_path / "memory_loadtitle.json"
        m.add_message("user", "Test chargement titre")
        loaded = ConversationMemory.load_from_file(m.memory_file)
        assert loaded.title == m.title

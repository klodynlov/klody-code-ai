"""Tests pour tools/llm_import.py — parsers ChatGPT/Claude/generic, détection techs."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_imports_dir(tmp_path, monkeypatch):
    """Redirige IMPORTS_DIR vers un dossier temporaire pour chaque test."""
    import tools.llm_import as m
    monkeypatch.setattr(m, "IMPORTS_DIR", tmp_path)
    return tmp_path


# ── _detect_format ─────────────────────────────────────────────────────────────

class TestDetectFormat:
    def test_chatgpt_detecte(self):
        from tools.llm_import import _detect_format
        data = [{"mapping": {}, "title": "Convo"}]
        assert _detect_format(data) == "chatgpt"

    def test_claude_detecte(self):
        from tools.llm_import import _detect_format
        data = [{"chat_messages": [], "uuid": "abc123"}]
        assert _detect_format(data) == "claude"

    def test_generic_list_detecte(self):
        from tools.llm_import import _detect_format
        data = [{"messages": []}]
        assert _detect_format(data) == "generic_list"

    def test_generic_dict_detecte(self):
        from tools.llm_import import _detect_format
        data = {"messages": []}
        assert _detect_format(data) == "generic_dict"

    def test_format_inconnu(self):
        from tools.llm_import import _detect_format
        data = [{"foo": "bar"}]
        assert _detect_format(data) == "unknown"

    def test_liste_vide_format_inconnu(self):
        from tools.llm_import import _detect_format
        assert _detect_format([]) == "unknown"


# ── _parse_chatgpt ─────────────────────────────────────────────────────────────

class TestParseChatGPT:
    def test_extrait_messages_user_et_assistant(self):
        from tools.llm_import import _parse_chatgpt
        data = [{
            "title": "Ma convo",
            "mapping": {
                "node1": {"message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["Bonjour"]},
                }},
                "node2": {"message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": ["Salut !"]},
                }},
            }
        }]
        result = _parse_chatgpt(data)
        assert len(result) == 1
        assert result[0]["title"] == "Ma convo"
        roles = {m["role"] for m in result[0]["messages"]}
        assert "user" in roles
        assert "assistant" in roles

    def test_ignore_roles_inconnus(self):
        from tools.llm_import import _parse_chatgpt
        data = [{
            "title": "Convo",
            "mapping": {
                "n1": {"message": {
                    "author": {"role": "system"},
                    "content": {"parts": ["Système"]},
                }},
                "n2": {"message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["Question"]},
                }},
            }
        }]
        result = _parse_chatgpt(data)
        roles = [m["role"] for m in result[0]["messages"]]
        assert "system" not in roles
        assert "user" in roles

    def test_ignore_messages_vides(self):
        from tools.llm_import import _parse_chatgpt
        data = [{
            "title": "Empty",
            "mapping": {
                "n1": {"message": {
                    "author": {"role": "user"},
                    "content": {"parts": [""]},
                }},
            }
        }]
        result = _parse_chatgpt(data)
        # Aucune conversation car message vide
        assert result == []

    def test_node_sans_message_ignore(self):
        from tools.llm_import import _parse_chatgpt
        data = [{"title": "T", "mapping": {"n1": {}}}]
        result = _parse_chatgpt(data)
        assert result == []


# ── _parse_claude ──────────────────────────────────────────────────────────────

class TestParseClaude:
    def test_extrait_messages_human_et_assistant(self):
        from tools.llm_import import _parse_claude
        data = [{
            "uuid": "abc",
            "name": "Ma session Claude",
            "chat_messages": [
                {"sender": "human", "text": "Salut"},
                {"sender": "assistant", "text": "Bonjour !"},
            ]
        }]
        result = _parse_claude(data)
        assert len(result) == 1
        assert result[0]["title"] == "Ma session Claude"
        roles = {m["role"] for m in result[0]["messages"]}
        assert "user" in roles
        assert "assistant" in roles

    def test_sender_human_mappe_en_user(self):
        from tools.llm_import import _parse_claude
        data = [{"uuid": "x", "chat_messages": [{"sender": "human", "text": "Coucou"}]}]
        result = _parse_claude(data)
        assert result[0]["messages"][0]["role"] == "user"

    def test_nom_fallback_sur_uuid(self):
        from tools.llm_import import _parse_claude
        data = [{"uuid": "my-uuid", "chat_messages": [{"sender": "human", "text": "hey"}]}]
        result = _parse_claude(data)
        assert result[0]["title"] == "my-uuid"

    def test_messages_vides_ignores(self):
        from tools.llm_import import _parse_claude
        data = [{"uuid": "x", "chat_messages": [{"sender": "human", "text": ""}]}]
        result = _parse_claude(data)
        assert result == []


# ── _parse_generic ─────────────────────────────────────────────────────────────

class TestParseGeneric:
    def test_parse_dict_avec_messages(self):
        from tools.llm_import import _parse_generic
        data = {"messages": [
            {"role": "user", "content": "Ma question"},
            {"role": "assistant", "content": "Ma réponse"},
        ]}
        result = _parse_generic(data)
        assert len(result) == 1

    def test_role_human_normalise_en_user(self):
        from tools.llm_import import _parse_generic
        data = {"messages": [{"role": "human", "content": "Hello"}]}
        result = _parse_generic(data)
        assert result[0]["messages"][0]["role"] == "user"

    def test_role_ai_normalise_en_assistant(self):
        from tools.llm_import import _parse_generic
        data = {"messages": [{"role": "ai", "content": "Response"}]}
        result = _parse_generic(data)
        assert result[0]["messages"][0]["role"] == "assistant"

    def test_content_liste_concatène(self):
        from tools.llm_import import _parse_generic
        data = {"messages": [{"role": "user", "content": [{"text": "A"}, {"text": "B"}]}]}
        result = _parse_generic(data)
        assert "A" in result[0]["messages"][0]["content"]
        assert "B" in result[0]["messages"][0]["content"]

    def test_role_inconnu_ignore(self):
        from tools.llm_import import _parse_generic
        data = {"messages": [{"role": "tool", "content": "output"}]}
        result = _parse_generic(data)
        assert result == []


# ── _count_techs ───────────────────────────────────────────────────────────────

class TestCountTechs:
    def test_python_detecte(self):
        from tools.llm_import import _count_techs
        msgs = ["J'utilise Python pour ce projet", "Python est super"]
        result = _count_techs(msgs)
        # "Python" doit être compté
        assert any("python" in k.lower() for k in result.keys())

    def test_plusieurs_techs_detectees(self):
        from tools.llm_import import _count_techs
        msgs = ["J'utilise React et TypeScript avec FastAPI et Docker"]
        result = _count_techs(msgs)
        techs_lower = {k.lower() for k in result.keys()}
        assert len(techs_lower) >= 2

    def test_tech_absente_non_comptee(self):
        from tools.llm_import import _count_techs
        msgs = ["J'aime les baguettes et le fromage"]
        result = _count_techs(msgs)
        assert result == {}

    def test_occurrences_cumulées(self):
        from tools.llm_import import _count_techs
        msgs = ["Python", "Python", "Python"]
        result = _count_techs(msgs)
        counts = {k.lower(): v for k, v in result.items()}
        assert counts.get("python", 0) == 3


# ── import_llm_export ──────────────────────────────────────────────────────────

class TestImportLlmExport:
    def test_fichier_inexistant_retourne_erreur(self, tmp_path):
        from tools.llm_import import import_llm_export
        result = import_llm_export("ghost.json")
        assert "ERREUR" in result

    def test_extension_non_json_retourne_erreur(self, tmp_path):
        from tools.llm_import import import_llm_export
        p = tmp_path / "export.txt"
        p.write_text("data")
        result = import_llm_export(str(p))
        assert "ERREUR" in result

    def test_json_invalide_retourne_erreur(self, tmp_path):
        from tools.llm_import import import_llm_export
        p = tmp_path / "bad.json"
        p.write_text("{ invalide json")
        result = import_llm_export(str(p))
        assert "ERREUR" in result or "invalide" in result.lower()

    def test_format_inconnu_retourne_erreur(self, tmp_path):
        from tools.llm_import import import_llm_export
        p = tmp_path / "weird.json"
        p.write_text(json.dumps({"foo": "bar"}))
        result = import_llm_export(str(p))
        assert "ERREUR" in result or "non reconnu" in result.lower()

    def test_export_claude_valide(self, tmp_path):
        from tools.llm_import import import_llm_export
        data = [{
            "uuid": "abc",
            "name": "Session de test",
            "chat_messages": [
                {"sender": "human", "text": "Comment utiliser Python avec FastAPI ?"},
                {"sender": "assistant", "text": "Tu peux créer une app avec..."},
            ]
        }]
        p = tmp_path / "export_claude.json"
        p.write_text(json.dumps(data))
        result = import_llm_export(str(p))
        assert "Session de test" in result or "conversations" in result.lower()
        assert "ERREUR" not in result

    def test_export_chatgpt_valide(self, tmp_path):
        from tools.llm_import import import_llm_export
        data = [{
            "title": "ChatGPT Session",
            "mapping": {
                "n1": {"message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["Explique React hooks"]},
                }},
            }
        }]
        p = tmp_path / "conversations.json"
        p.write_text(json.dumps(data))
        result = import_llm_export(str(p))
        assert "ERREUR" not in result
        assert "conversation" in result.lower()

    def test_fichier_vide_de_messages(self, tmp_path):
        from tools.llm_import import import_llm_export
        data = [{"uuid": "x", "chat_messages": []}]
        p = tmp_path / "empty.json"
        p.write_text(json.dumps(data))
        result = import_llm_export(str(p))
        assert "Aucun" in result or "ERREUR" in result

    def test_sortie_tronquee_a_max_chars(self, tmp_path, monkeypatch):
        """Le résultat ne doit jamais dépasser MAX_CHARS."""
        from tools.llm_import import import_llm_export
        import tools.llm_import as m
        monkeypatch.setattr(m, "MAX_CHARS", 100)
        data = [{
            "uuid": "x",
            "chat_messages": [
                {"sender": "human", "text": "A" * 5000},
            ]
        }]
        p = tmp_path / "big.json"
        p.write_text(json.dumps(data))
        result = import_llm_export(str(p))
        assert len(result) <= 100

    def test_chemin_relatif_resolu_dans_imports_dir(self, tmp_path):
        """Un chemin relatif doit être cherché dans IMPORTS_DIR."""
        from tools.llm_import import import_llm_export
        data = [{"uuid": "x", "chat_messages": [{"sender": "human", "text": "test"}]}]
        (tmp_path / "mon_export.json").write_text(json.dumps(data))
        # Passe un nom relatif, pas un chemin absolu
        result = import_llm_export("mon_export.json")
        assert "ERREUR" not in result

    def test_meme_racine_que_write_file(self, tmp_path, monkeypatch):
        """Régression (capture 29/05) : write_file écrit 'imports/<uuid>.json'
        puis import_llm_export sur LE MÊME chemin disait « introuvable » car
        IMPORTS_DIR était ancré sur la racine du code, pas sur PROJECT_ROOT.
        Les deux outils doivent partager PROJECT_ROOT."""
        import tools.llm_import as m
        from tools.file_manager import FileManager
        monkeypatch.setattr(m, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(m, "IMPORTS_DIR", tmp_path / "imports")

        data = [{"uuid": "x", "name": "Projet", "chat_messages": [
            {"sender": "human", "text": "Décris le projet zouk RVC"}]}]
        # write_file ancre sur PROJECT_ROOT, exactement comme dans l'app
        FileManager(root=tmp_path).write_file(
            "imports/019dfffb-1a2c-71db.json", json.dumps(data)
        )
        # import sur LE MÊME chemin relatif (avec le préfixe imports/)
        result = m.import_llm_export("imports/019dfffb-1a2c-71db.json")
        assert "ERREUR" not in result
        assert "Projet" in result or "conversation" in result.lower()


# ── list_imports ───────────────────────────────────────────────────────────────

class TestListImports:
    def test_aucun_fichier_retourne_message(self, tmp_path):
        from tools.llm_import import list_imports
        result = list_imports()
        assert "Aucun" in result

    def test_liste_les_fichiers_json(self, tmp_path):
        from tools.llm_import import list_imports
        (tmp_path / "export1.json").write_text("{}")
        (tmp_path / "export2.json").write_text("{}")
        result = list_imports()
        assert "export1.json" in result
        assert "export2.json" in result

    def test_ignore_non_json(self, tmp_path):
        from tools.llm_import import list_imports
        (tmp_path / "readme.txt").write_text("doc")
        result = list_imports()
        assert "readme.txt" not in result

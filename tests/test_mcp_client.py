"""Tests pour tools/mcp_client.py — _is_domain_file, get_skills, _parse_result, search_books."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── _is_domain_file ────────────────────────────────────────────────────────────

class TestIsDomainFile:
    def test_fichier_valide_liste_avec_title(self, tmp_path):
        from tools.mcp_client import _is_domain_file
        p = tmp_path / "python.json"
        p.write_text(json.dumps([{"title": "T", "content": "C", "tags": ["python"]}]))
        assert _is_domain_file(p) is True

    def test_dict_user_skill_non_domaine(self, tmp_path):
        from tools.mcp_client import _is_domain_file
        p = tmp_path / "user.json"
        p.write_text(json.dumps({"name": "N", "slug": "n", "content": "c"}))
        assert _is_domain_file(p) is False

    def test_liste_vide_non_domaine(self, tmp_path):
        from tools.mcp_client import _is_domain_file
        p = tmp_path / "empty.json"
        p.write_text(json.dumps([]))
        assert _is_domain_file(p) is False

    def test_liste_sans_title_non_domaine(self, tmp_path):
        from tools.mcp_client import _is_domain_file
        p = tmp_path / "bad.json"
        p.write_text(json.dumps([{"content": "c", "tags": []}]))
        assert _is_domain_file(p) is False

    def test_json_invalide_retourne_false(self, tmp_path):
        from tools.mcp_client import _is_domain_file
        p = tmp_path / "broken.json"
        p.write_text("{ invalide")
        assert _is_domain_file(p) is False

    def test_fichier_inexistant_retourne_false(self, tmp_path):
        from tools.mcp_client import _is_domain_file
        p = tmp_path / "ghost.json"
        assert _is_domain_file(p) is False


# ── get_skills ─────────────────────────────────────────────────────────────────

class TestGetSkills:
    @pytest.fixture(autouse=True)
    def patch_skills_dir(self, tmp_path, monkeypatch):
        import tools.mcp_client as m
        monkeypatch.setattr(m, "SKILLS_DIR", tmp_path)
        # Créer un domaine valide
        (tmp_path / "python.json").write_text(json.dumps([
            {"title": "Type hints", "content": "Utiliser les type hints.", "tags": ["python", "typing"]},
            {"title": "Async", "content": "Préférer asyncio.", "tags": ["async"]},
        ]))
        # Créer un user skill (dict — ne doit pas être exposé comme domaine)
        (tmp_path / "utilisateur_profil.json").write_text(json.dumps(
            {"name": "Profil", "slug": "utilisateur_profil", "description": "d", "content": "c"}
        ))

    def test_domaine_valide_retourne_conventions(self):
        from tools.mcp_client import get_skills
        result = get_skills("python")
        assert "Convention" in result or "## " in result
        assert "Type hints" in result

    def test_domaine_valide_contient_contenu(self):
        from tools.mcp_client import get_skills
        result = get_skills("python")
        assert "type hints" in result.lower() or "Utiliser" in result

    def test_domaine_valide_contient_tags(self):
        from tools.mcp_client import get_skills
        result = get_skills("python")
        assert "python" in result.lower()

    def test_domaine_inconnu_retourne_message_erreur(self):
        from tools.mcp_client import get_skills
        result = get_skills("java")
        assert "inconnu" in result
        assert "Domaines disponibles" in result

    def test_domaine_inconnu_liste_seulement_vrais_domaines(self):
        from tools.mcp_client import get_skills
        result = get_skills("java")
        assert "utilisateur_profil" not in result
        assert "python" in result

    def test_user_skill_slug_retourne_inconnu(self):
        from tools.mcp_client import get_skills
        result = get_skills("utilisateur_profil")
        assert "inconnu" in result

    def test_domaine_inexistant_retourne_inconnu(self):
        from tools.mcp_client import get_skills
        result = get_skills("inexistant_xyz")
        assert "inconnu" in result


# ── _parse_result ──────────────────────────────────────────────────────────────

class TestParseResult:
    def test_found_false_retourne_aucun_resultat(self):
        from tools.mcp_client import _parse_result
        data = {"found": False, "answer": "", "sources": []}
        result = _parse_result(data, limit=3)
        assert "Aucun résultat" in result

    def test_found_true_retourne_answer(self):
        from tools.mcp_client import _parse_result
        data = {
            "found": True,
            "answer": "Les design patterns sont...",
            "sources": [{"title": "Clean Code", "author": "Martin", "page": 42}],
        }
        result = _parse_result(data, limit=3)
        assert "design patterns" in result

    def test_sources_formatées(self):
        from tools.mcp_client import _parse_result
        data = {
            "found": True,
            "answer": "réponse",
            "sources": [
                {"title": "Livre A", "author": "Auteur A", "page": 10},
                {"title": "Livre B", "author": "Auteur B", "page": 20},
            ],
        }
        result = _parse_result(data, limit=3)
        assert "Livre A" in result
        assert "Auteur A" in result
        assert "p.10" in result

    def test_limit_respectee_dans_sources(self):
        from tools.mcp_client import _parse_result
        sources = [{"title": f"Livre {i}", "author": "A", "page": i} for i in range(10)]
        data = {"found": True, "answer": "réponse", "sources": sources}
        result = _parse_result(data, limit=2)
        # Seules 2 sources max
        assert result.count("Livre") <= 2

    def test_answer_vide_avec_found_true(self):
        from tools.mcp_client import _parse_result
        data = {"found": True, "answer": "", "sources": []}
        result = _parse_result(data, limit=3)
        assert result  # ne doit pas être vide

    def test_found_manquant_traite_comme_false(self):
        from tools.mcp_client import _parse_result
        data = {"answer": "réponse", "sources": []}
        result = _parse_result(data, limit=3)
        assert "Aucun résultat" in result


# ── search_books ───────────────────────────────────────────────────────────────

class TestSearchBooks:
    def test_connect_error_retourne_message_lisible(self):
        from tools.mcp_client import search_books
        import httpx
        with patch("tools.mcp_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError("refused")
            result = search_books("design patterns")
        assert "inaccessible" in result.lower() or "LibraryBrain" in result

    def test_http_error_retourne_message_lisible(self):
        from tools.mcp_client import search_books
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("tools.mcp_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = (
                httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp)
            )
            result = search_books("design patterns")
        assert "503" in result or "erreur" in result.lower()

    def test_job_done_avec_found_true(self):
        from tools.mcp_client import search_books
        # Mock : soumission job + poll retourne done immédiatement
        submit_resp = MagicMock()
        submit_resp.json.return_value = {"job_id": "abc123"}
        submit_resp.raise_for_status = MagicMock()

        poll_resp = MagicMock()
        poll_resp.raise_for_status = MagicMock()
        poll_resp.json.return_value = {
            "status": "done",
            "result": {
                "found": True,
                "answer": "Les patterns sont...",
                "sources": [{"title": "Book", "author": "Auth", "page": 1}],
            }
        }

        with patch("tools.mcp_client.httpx.Client") as mock_client:
            inst = mock_client.return_value.__enter__.return_value
            inst.post.return_value = submit_resp
            inst.get.return_value = poll_resp
            with patch("tools.mcp_client.time.sleep"):
                result = search_books("design patterns", limit=3)

        assert "patterns" in result.lower() or "Book" in result

    def test_job_error_retourne_message(self):
        from tools.mcp_client import search_books
        submit_resp = MagicMock()
        submit_resp.json.return_value = {"job_id": "xyz"}
        submit_resp.raise_for_status = MagicMock()

        poll_resp = MagicMock()
        poll_resp.raise_for_status = MagicMock()
        poll_resp.json.return_value = {"status": "error", "error": "LLM crash"}

        with patch("tools.mcp_client.httpx.Client") as mock_client:
            inst = mock_client.return_value.__enter__.return_value
            inst.post.return_value = submit_resp
            inst.get.return_value = poll_resp
            with patch("tools.mcp_client.time.sleep"):
                result = search_books("query")

        assert "erreur" in result.lower() or "LLM crash" in result

    def test_timeout_retourne_message(self):
        from tools.mcp_client import search_books
        submit_resp = MagicMock()
        submit_resp.json.return_value = {"job_id": "xyz"}
        submit_resp.raise_for_status = MagicMock()

        poll_resp = MagicMock()
        poll_resp.raise_for_status = MagicMock()
        poll_resp.json.return_value = {"status": "pending"}

        with patch("tools.mcp_client.httpx.Client") as mock_client:
            inst = mock_client.return_value.__enter__.return_value
            inst.post.return_value = submit_resp
            inst.get.return_value = poll_resp
            with patch("tools.mcp_client.time.sleep"):
                with patch("tools.mcp_client._POLL_MAX", 2):
                    result = search_books("query")

        assert "timeout" in result.lower()

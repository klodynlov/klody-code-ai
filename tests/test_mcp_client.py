"""Tests pour tools/mcp_client.py — _is_domain_file, get_skills, _parse_result, search_books."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
        import httpx
        from tools.mcp_client import search_books
        with patch("tools.mcp_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError("refused")
            result = search_books("design patterns")
        assert "inaccessible" in result.lower() or "LibraryBrain" in result

    def test_http_error_retourne_message_lisible(self):
        import httpx
        from tools.mcp_client import search_books
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("tools.mcp_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = (
                httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp)
            )
            result = search_books("design patterns")
        assert "503" in result or "erreur" in result.lower()

    def test_tape_bien_la_route_api_ask(self):
        """Épingle l'URL appelée.

        Régression du 16/07 : le client soumettait un job sur POST /api/ask/job,
        route supprimée côté LibraryBrain → 404 en prod. Les tests restaient verts
        car ils mockaient la route morte. On asserte donc l'URL, pas juste le parsing.
        """
        from tools.mcp_client import LIBRARYBRAIN_URL, search_books
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"found": False}

        with patch("tools.mcp_client.httpx.Client") as mock_client:
            inst = mock_client.return_value.__enter__.return_value
            inst.post.return_value = resp
            search_books("query")

        assert inst.post.call_args.args[0] == LIBRARYBRAIN_URL
        assert inst.post.call_args.args[0].endswith("/api/ask")
        # L'archi job/polling est morte : aucun GET de sonde ne doit subsister.
        inst.get.assert_not_called()

    def test_reponse_found_true_formatee(self):
        from tools.mcp_client import search_books
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "found": True,
            "answer": "Les patterns sont...",
            "sources": [{"title": "Book", "author": "Auth", "page": 1}],
        }

        with patch("tools.mcp_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp
            result = search_books("design patterns", limit=3)

        assert "patterns" in result.lower()
        assert "Book" in result

    def test_found_false_retourne_aucun_resultat(self):
        from tools.mcp_client import search_books
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"found": False, "answer": None, "sources": []}

        with patch("tools.mcp_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp
            result = search_books("query")

        assert "aucun résultat" in result.lower()

    def test_timeout_retourne_message(self):
        import httpx
        from tools.mcp_client import search_books
        with patch("tools.mcp_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = (
                httpx.ReadTimeout("trop lent")
            )
            result = search_books("query")

        assert "timeout" in result.lower()

    def test_401_nomme_la_cause_auth(self):
        """Un 401 doit désigner la cause d'AUTH, pas se lire comme une panne
        réseau — et distinguer « token absent » de « token refusé ».

        L'assertion d'origine (`"api_token" in result or "X-API-Token" in result`)
        était trop lâche pour voir son propre message devenir faux : elle est
        restée verte quand Klody s'est mis à ENVOYER le token, alors que le
        message affirmait toujours « Klody n'envoie pas d'en-tête X-API-Token ».
        On épingle donc les deux branches, et on PIN le token (sinon un .env de
        dev choisirait la branche à notre place).
        """
        import config
        import httpx
        from tools.mcp_client import search_books

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        def _call_401() -> str:
            with patch("tools.mcp_client.httpx.Client") as mock_client:
                mock_client.return_value.__enter__.return_value.post.side_effect = (
                    httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp)
                )
                return search_books("query")

        with patch.object(config, "LIBRARYBRAIN_TOKEN", ""):
            absent = _call_401()
        with patch.object(config, "LIBRARYBRAIN_TOKEN", "un-token-qui-ne-passe-pas"):
            refuse = _call_401()

        assert "401" in absent and "401" in refuse
        assert "api_token" in absent, "doit nommer la clé serveur à renseigner"
        assert "LIBRARYBRAIN_TOKEN est vide" in absent
        assert "ne correspond pas" in refuse
        assert absent != refuse, "un token refusé ne se soigne pas comme un token absent"


# ── catalog_lookup ───────────────────────────────────────────────────────────

class TestCatalogLookup:
    """DB SQLite réelle (books + books_fts) pour valider le lookup non gaté."""

    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        import sqlite3
        path = tmp_path / "library_brain.db"
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE books (
                id INTEGER PRIMARY KEY, title TEXT, author TEXT, year INTEGER,
                page_count INTEGER, format TEXT, indexed_at TEXT
            );
            CREATE VIRTUAL TABLE books_fts USING fts5(
                title, author, content='books', content_rowid='id'
            );
            """
        )
        con.execute(
            "INSERT INTO books VALUES (?,?,?,?,?,?,?)",
            (1, "Animation Craft For 3D and 2D Animators", "Jonathan Annand",
             None, 345, "pdf", "2026-06-18T11:09:26"),
        )
        con.execute(
            "INSERT INTO books VALUES (?,?,?,?,?,?,?)",
            (2, "Clean Code", "Robert Martin", 2008, 464, "epub", "2026-01-02T00:00:00"),
        )
        con.execute("INSERT INTO books_fts(books_fts) VALUES ('rebuild')")
        con.commit()
        con.close()
        monkeypatch.setattr("tools.mcp_client.LIBRARY_DB_PATH", path)
        return path

    def test_trouve_livre_par_titre(self, db):
        from tools.mcp_client import catalog_lookup
        result = catalog_lookup("animation craft")
        assert "Animation Craft" in result
        assert "Jonathan Annand" in result
        assert "indexé le 2026-06-18" in result

    def test_trouve_livre_par_auteur(self, db):
        from tools.mcp_client import catalog_lookup
        result = catalog_lookup("Robert Martin")
        assert "Clean Code" in result

    def test_livre_absent_dit_pas_indexe(self, db):
        from tools.mcp_client import catalog_lookup
        result = catalog_lookup("Seigneur des Anneaux Tolkien")
        assert "pas indexé" in result
        assert "2 livres" in result  # total du catalogue

    def test_db_absente_message_lisible(self, tmp_path, monkeypatch):
        from tools.mcp_client import catalog_lookup
        monkeypatch.setattr("tools.mcp_client.LIBRARY_DB_PATH", tmp_path / "ghost.db")
        result = catalog_lookup("anything")
        assert "introuvable" in result

    def test_match_partiel_signale_approchant(self, db):
        # « clean » matche Clean Code, « tolkien » non → OR → approchant, pas exact
        from tools.mcp_client import catalog_lookup
        result = catalog_lookup("clean tolkien")
        assert "approchant" in result.lower()
        assert "Clean Code" in result

    def test_requete_vide(self, db):
        from tools.mcp_client import catalog_lookup
        result = catalog_lookup("!!! ?")
        assert "vide" in result.lower()

    def test_que_des_mots_vides_dit_vide(self, db):
        from tools.mcp_client import catalog_lookup
        result = catalog_lookup("as-tu le livre")
        assert "vide" in result.lower()

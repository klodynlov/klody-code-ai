"""Tests pour tools/sql_runner — exécution SQL locale sandboxée (Roadmap v2 #10).

Couvre le fonctionnel ET la preuve de sécurité : chaque vecteur d'évasion du
sandbox identifié au threat-model (ATTACH, VACUUM INTO, load_extension, écriture
en mode read, injection d'URI, hors-racines, bombe mémoire, multi-statements)
doit être bloqué.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from tools import sql_runner
from tools.sql_runner import format_sql_result, run_sql


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    """Crée une base SQLite peuplée sous une racine autorisée (tmp_path)."""
    path = tmp_path / "app.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO users (id, name) VALUES (?, ?)",
                    [(1, "alice"), (2, "bob"), (3, "carol")])
    con.commit()
    con.close()
    monkeypatch.setattr(sql_runner, "_SQL_ROOTS", [tmp_path.resolve()])
    return path


class TestLectureNominale:
    def test_select_renvoie_lignes(self, db):
        res = run_sql("SELECT id, name FROM users ORDER BY id", str(db))
        assert res["ok"] is True
        assert res["columns"] == ["id", "name"]
        assert res["rows"][0] == [1, "alice"]
        assert res["rowcount"] == 3

    def test_params_binding(self, db):
        res = run_sql("SELECT name FROM users WHERE id = ?", str(db), params=[2])
        assert res["ok"] is True
        assert res["rows"] == [["bob"]]

    def test_max_rows_tronque(self, db):
        res = run_sql("SELECT id FROM users ORDER BY id", str(db), max_rows=2)
        assert res["rowcount"] == 2
        assert res["truncated"] is True

    def test_schema_via_sqlite_master(self, db):
        res = run_sql("SELECT name FROM sqlite_master WHERE type='table'", str(db))
        assert res["ok"] is True
        assert ["users"] in res["rows"]


class TestConfinementChemin:
    def test_hors_racines_refuse(self, db):
        res = run_sql("SELECT 1", "/etc/passwd")
        assert res["ok"] is False
        assert "hors des racines" in res["error"] or "bloqué" in res["error"]

    def test_uri_refusee(self, db):
        res = run_sql("SELECT 1", "file:/etc/x?mode=rwc")
        assert res["ok"] is False
        assert "URI" in res["error"]

    def test_point_interrogation_refuse(self, db):
        res = run_sql("SELECT 1", str(db) + "?mode=rwc")
        assert res["ok"] is False

    def test_extension_sensible_bloquee(self, tmp_path, monkeypatch):
        secret = tmp_path / "creds.env"
        secret.write_text("x")
        monkeypatch.setattr(sql_runner, "_SQL_ROOTS", [tmp_path.resolve()])
        res = run_sql("SELECT 1", str(secret))
        assert res["ok"] is False
        assert "bloqué" in res["error"]

    def test_base_inexistante_refuse(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sql_runner, "_SQL_ROOTS", [tmp_path.resolve()])
        res = run_sql("SELECT 1", str(tmp_path / "nope.db"))
        assert res["ok"] is False
        assert "introuvable" in res["error"]


class TestModeEcriture:
    def test_write_bloque_par_defaut(self, db, monkeypatch):
        monkeypatch.setattr(sql_runner, "SQL_WRITE_ENABLED", False)
        res = run_sql("INSERT INTO users (id, name) VALUES (9, 'zoe')", str(db), mode="write")
        assert res["ok"] is False
        assert "désactivé" in res["error"]

    def test_ecriture_en_mode_read_refusee(self, db):
        res = run_sql("CREATE TABLE evil (x)", str(db), mode="read")
        assert res["ok"] is False

    def test_write_autorise_si_flag(self, db, monkeypatch):
        monkeypatch.setattr(sql_runner, "SQL_WRITE_ENABLED", True)
        ins = run_sql("INSERT INTO users (id, name) VALUES (9, 'zoe')", str(db), mode="write")
        assert ins["ok"] is True
        chk = run_sql("SELECT name FROM users WHERE id = 9", str(db))
        assert chk["rows"] == [["zoe"]]


class TestEvasionSandbox:
    """Chaque vecteur du threat-model doit être neutralisé."""

    def test_attach_refuse_en_lecture(self, db, tmp_path):
        res = run_sql(f"ATTACH DATABASE '{tmp_path / 'x.db'}' AS x", str(db))
        assert res["ok"] is False

    def test_attach_refuse_meme_en_ecriture(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(sql_runner, "SQL_WRITE_ENABLED", True)
        res = run_sql(f"ATTACH DATABASE '{tmp_path / 'x.db'}' AS x", str(db), mode="write")
        assert res["ok"] is False

    def test_vacuum_into_refuse(self, db, tmp_path, monkeypatch):
        # VACUUM INTO écrirait une copie hors sandbox — même en écriture activée.
        monkeypatch.setattr(sql_runner, "SQL_WRITE_ENABLED", True)
        target = tmp_path / "leak.db"
        res = run_sql(f"VACUUM INTO '{target}'", str(db), mode="write")
        assert res["ok"] is False
        assert not target.exists()

    def test_load_extension_refuse(self, db):
        res = run_sql("SELECT load_extension('/tmp/evil.so')", str(db))
        assert res["ok"] is False

    def test_bombe_memoire_plafonnee(self, db):
        # randomblob(1e9) : sans SQLITE_LIMIT_LENGTH ce serait un OOM ; ici → erreur rapide.
        res = run_sql("SELECT randomblob(1000000000)", str(db))
        assert res["ok"] is False

    def test_multi_statements_refuse(self, db):
        res = run_sql("SELECT 1; DROP TABLE users", str(db))
        assert res["ok"] is False


class TestValidationEntree:
    def test_mode_invalide(self, db):
        res = run_sql("SELECT 1", str(db), mode="delete")
        assert res["ok"] is False
        assert "mode invalide" in res["error"]

    def test_requete_vide(self, db):
        res = run_sql("   ", str(db))
        assert res["ok"] is False


class TestFormat:
    def test_format_select(self, db):
        out = format_sql_result(run_sql("SELECT id, name FROM users ORDER BY id", str(db)))
        assert "id | name" in out
        assert "alice" in out

    def test_format_erreur(self):
        assert format_sql_result({"ok": False, "error": "boom"}) == "boom"

    def test_format_write(self):
        out = format_sql_result({"ok": True, "mode": "write", "database": "x.db",
                                 "columns": [], "rows": [], "rowcount": 1, "truncated": False})
        assert "Lignes affectées : 1" in out

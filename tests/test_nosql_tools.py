"""Tests pour scaffold_nosql — génération de repository MongoDB typé (#10)."""
from __future__ import annotations

import pytest
from tools.scaffold_tools import format_scaffold_result, scaffold_nosql


class TestGeneration:
    def test_code_compile(self):
        res = scaffold_nosql("user", [{"name": "email", "type": "str"},
                                      {"name": "age", "type": "int"}])
        assert res["ok"] is True and res["backend"] == "mongodb"
        compile(res["code"], "<gen>", "exec")
        assert res["filename"] == "user_repository.py"

    def test_repository_idiomatique(self):
        code = scaffold_nosql("product", [{"name": "price", "type": "float"}])["code"]
        assert "from pymongo.collection import Collection" in code
        assert "from bson import ObjectId" in code
        assert "@dataclass" in code
        assert "class Product:" in code and "class ProductRepository:" in code
        assert "price: float" in code
        assert "'products'" in code
        for op in ("insert_one", "find_one", "update_one", "delete_one", "find_by"):
            assert op in code
        assert "ObjectId(oid)" in code

    def test_datetime_conditionnel(self):
        sans = scaffold_nosql("a", [{"name": "x", "type": "int"}])["code"]
        avec = scaffold_nosql("b", [{"name": "t", "type": "datetime"}])["code"]
        assert "from datetime import datetime" not in sans
        assert "from datetime import datetime" in avec
        compile(avec, "<gen>", "exec")


class TestValidation:
    def test_backend_non_supporte(self):
        assert scaffold_nosql("user", backend="cassandra")["ok"] is False

    @pytest.mark.parametrize("bad", ["1abc", "a-b", "a b", ""])
    def test_ressource_invalide(self, bad):
        assert scaffold_nosql(bad)["ok"] is False

    def test_type_hors_allowlist(self):
        assert scaffold_nosql("x", [{"name": "d", "type": "blob"}])["ok"] is False


class TestFormat:
    def test_format_ok(self):
        out = format_scaffold_result(scaffold_nosql("user"))
        assert "repository MongoDB" in out and "user_repository.py" in out

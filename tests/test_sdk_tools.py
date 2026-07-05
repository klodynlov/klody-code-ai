"""Tests pour scaffold_sdk — génération de client SDK typé (#10)."""
from __future__ import annotations

import pytest
from tools.scaffold_tools import format_scaffold_result, scaffold_sdk


class TestGeneration:
    def test_code_compile(self):
        res = scaffold_sdk("user", [{"name": "email", "type": "str"},
                                    {"name": "age", "type": "int"}])
        assert res["ok"] is True and res["language"] == "python"
        compile(res["code"], "<gen>", "exec")
        assert res["filename"] == "user_client.py"

    def test_client_idiomatique(self):
        code = scaffold_sdk("product", [{"name": "price", "type": "float"}])["code"]
        assert "import httpx" in code
        assert "@dataclass" in code
        assert "class Product:" in code
        assert "class ProductClient:" in code
        assert "httpx.Client(base_url=base_url.rstrip" in code
        assert "id: int" in code and "price: float" in code
        for meth in ("def list", "def get", "def create", "def update",
                     "def delete", "def close"):
            assert meth in code
        assert '"/products"' in code

    def test_datetime_conditionnel(self):
        sans = scaffold_sdk("a", [{"name": "x", "type": "int"}])["code"]
        avec = scaffold_sdk("b", [{"name": "t", "type": "datetime"}])["code"]
        assert "from datetime import datetime" not in sans
        assert "from datetime import datetime" in avec
        compile(avec, "<gen>", "exec")

    def test_pascalcase(self):
        code = scaffold_sdk("order_item", [{"name": "qty", "type": "int"}])["code"]
        assert "class OrderItem:" in code and "class OrderItemClient:" in code
        assert '"/order_items"' in code


class TestValidation:
    def test_langage_non_supporte(self):
        assert scaffold_sdk("user", language="typescript")["ok"] is False

    @pytest.mark.parametrize("bad", ["1abc", "a-b", "a b", ""])
    def test_ressource_invalide(self, bad):
        assert scaffold_sdk(bad)["ok"] is False

    def test_id_reserve(self):
        assert scaffold_sdk("x", [{"name": "id", "type": "int"}])["ok"] is False

    def test_type_hors_allowlist(self):
        assert scaffold_sdk("x", [{"name": "d", "type": "blob"}])["ok"] is False


class TestFormat:
    def test_format_ok(self):
        out = format_scaffold_result(scaffold_sdk("user"))
        assert "SDK client Python" in out and "user_client.py" in out

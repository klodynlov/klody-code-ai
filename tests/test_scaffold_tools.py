"""Tests pour tools/scaffold_tools — scaffolding d'API REST FastAPI (#10)."""
from __future__ import annotations

import pytest
from tools.scaffold_tools import format_scaffold_result, scaffold_api


class TestGeneration:
    def test_code_compile(self):
        res = scaffold_api("user", [{"name": "email", "type": "str"},
                                    {"name": "age", "type": "int"}])
        assert res["ok"] is True
        compile(res["code"], "<gen>", "exec")  # doit compiler
        assert res["filename"] == "user_api.py"

    def test_contenu_idiomatique(self):
        code = scaffold_api("product", [{"name": "price", "type": "float"}])["code"]
        assert "class Product(" in code
        assert "class ProductBase(BaseModel)" in code
        assert "price: float" in code
        assert 'APIRouter(prefix="/products"' in code
        for verb in ("list_products", "get_product", "create_product",
                     "update_product", "delete_product"):
            assert f"def {verb}" in code

    def test_datetime_import_conditionnel(self):
        sans = scaffold_api("a", [{"name": "x", "type": "int"}])["code"]
        avec = scaffold_api("b", [{"name": "t", "type": "datetime"}])["code"]
        assert "from datetime import datetime" not in sans
        assert "from datetime import datetime" in avec
        compile(avec, "<gen>", "exec")

    def test_sans_champ_defaut_valide(self):
        res = scaffold_api("thing")
        assert res["ok"] is True
        compile(res["code"], "<gen>", "exec")
        assert "name: str" in res["code"]

    def test_nom_compose_en_pascalcase(self):
        code = scaffold_api("order_item", [{"name": "qty", "type": "int"}])["code"]
        assert "class OrderItem(" in code
        assert 'prefix="/order_items"' in code


class TestGraphQL:
    def test_schema_compile(self):
        res = scaffold_api("user", [{"name": "email", "type": "str"},
                                    {"name": "age", "type": "int"}], framework="graphql")
        assert res["ok"] is True and res["framework"] == "graphql"
        compile(res["code"], "<gen>", "exec")
        assert res["filename"] == "user_schema.py"

    def test_schema_idiomatique(self):
        code = scaffold_api("product", [{"name": "price", "type": "float"}],
                            framework="graphql")["code"]
        assert "import strawberry" in code
        assert "@strawberry.type" in code and "@strawberry.input" in code
        assert "class Product:" in code and "class ProductInput:" in code
        assert "id: int" in code  # le type porte id…
        # …et l'input ne porte QUE les champs métier (price), pas id.
        input_block = code.split("class ProductInput:")[1].split("_store")[0]
        assert "price: float" in input_block and "id: int" not in input_block
        for op in ("def products", "def product", "def create_product",
                   "def update_product", "def delete_product"):
            assert op in code
        assert "strawberry.Schema(query=Query, mutation=Mutation)" in code

    def test_graphql_datetime_conditionnel(self):
        code = scaffold_api("evt", [{"name": "at", "type": "datetime"}],
                            framework="graphql")["code"]
        assert "from datetime import datetime" in code
        compile(code, "<gen>", "exec")


class TestValidation:
    @pytest.mark.parametrize("bad", ["1abc", "a-b", "a b", "", "a.b", "é"])
    def test_ressource_invalide(self, bad):
        assert scaffold_api(bad)["ok"] is False

    def test_ressource_normalisee_en_minuscules(self):
        # "Bad" est toléré : normalisé en "bad".
        res = scaffold_api("Bad")
        assert res["ok"] is True and "class Bad(" in res["code"]

    def test_id_reserve(self):
        assert scaffold_api("x", [{"name": "id", "type": "int"}])["ok"] is False

    def test_type_hors_allowlist(self):
        res = scaffold_api("x", [{"name": "data", "type": "blob"}])
        assert res["ok"] is False and "Type" in res["error"]

    def test_champ_duplique(self):
        res = scaffold_api("x", [{"name": "a", "type": "int"}, {"name": "a", "type": "str"}])
        assert res["ok"] is False

    def test_trop_de_champs(self):
        many = [{"name": f"f{i}", "type": "int"} for i in range(40)]
        assert scaffold_api("x", many)["ok"] is False

    def test_framework_non_supporte(self):
        assert scaffold_api("x", framework="django")["ok"] is False

    def test_fields_mauvais_type(self):
        assert scaffold_api("x", fields="oops")["ok"] is False


class TestFormat:
    def test_format_ok(self):
        out = format_scaffold_result(scaffold_api("user"))
        assert "```python" in out and "user_api.py" in out

    def test_format_erreur(self):
        assert format_scaffold_result({"ok": False, "error": "boom"}) == "boom"

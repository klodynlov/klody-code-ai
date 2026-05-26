"""Tests pour les parsers de tool calls de LLMClient.

Couvre les 4 formats émis par les modèles + cas mixtes (texte + appel) :
1. JSON pur          : `{"name": "tool", "arguments": {...}}`
2. Compact           : `tool [param] {"key": "val"}`
3. XML-like (Qwen3)  : `<function=tool><parameter=p>val</parameter></function>`
4. JSON cassé        : guillemets internes non-échappés (docstrings Python)
"""
import json

import pytest

from agent.llm import LLMClient


@pytest.fixture
def client():
    """Instance non-initialisée (on ne teste que les helpers statiques/de parsing)."""
    return LLMClient.__new__(LLMClient)


@pytest.fixture
def valid_tools():
    return {"read_file", "write_file", "list_files", "search_in_files"}


# ── Format 1 : JSON pur ────────────────────────────────────────────────────────


class TestJsonPur:
    def test_objet_unique(self, client, valid_tools):
        text = '{"name": "read_file", "arguments": {"path": "x.py"}}'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"path": "x.py"}

    def test_liste_d_appels(self, client, valid_tools):
        text = '[{"name": "read_file", "arguments": {"path": "a"}}, {"name": "write_file", "arguments": {"path": "b", "content": "c"}}]'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 2
        assert {c["function"]["name"] for c in calls} == {"read_file", "write_file"}

    def test_bloc_markdown(self, client, valid_tools):
        text = '```json\n{"name": "read_file", "arguments": {"path": "x.py"}}\n```'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 1

    def test_outil_invalide_rejete(self, client, valid_tools):
        text = '{"name": "rm_rf_root", "arguments": {}}'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is None

    def test_parameters_au_lieu_de_arguments(self, client, valid_tools):
        # Certains modèles utilisent "parameters" au lieu de "arguments"
        text = '{"name": "read_file", "parameters": {"path": "x.py"}}'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"path": "x.py"}


# ── Format 2 : Compact `tool [param] {json}` ───────────────────────────────────


class TestFormatCompact:
    def test_compact_simple(self, client, valid_tools):
        text = 'read_file [path] {"path":"app.py"}'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"
        assert json.loads(calls[0]["function"]["arguments"]) == {"path": "app.py"}

    def test_compact_avec_plusieurs_params(self, client, valid_tools):
        text = 'write_file [path, content] {"path":"x.py","content":"hello"}'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 1
        assert calls[0]["function"]["name"] == "write_file"

    def test_compact_sans_crochets(self, client, valid_tools):
        # Format encore plus minimal
        text = 'read_file {"path":"x.py"}'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 1

    def test_compact_outil_inconnu_rejete(self, client, valid_tools):
        text = 'unknown_tool [p] {"p":"v"}'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is None


# ── Format 3 : XML-like (Qwen3-Coder) ──────────────────────────────────────────


class TestFormatXml:
    def test_xml_simple(self, client, valid_tools):
        text = '<function=read_file>\n<parameter=path>\nscript.py\n</parameter>\n</function>'
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"
        assert json.loads(calls[0]["function"]["arguments"]) == {"path": "script.py"}

    def test_xml_plusieurs_params(self, client, valid_tools):
        text = (
            '<function=write_file>'
            '<parameter=path>x.py</parameter>'
            '<parameter=content>print(1)</parameter>'
            '</function>'
        )
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert args == {"path": "x.py", "content": "print(1)"}

    def test_xml_plusieurs_appels(self, client, valid_tools):
        text = (
            '<function=read_file><parameter=path>a.py</parameter></function>'
            '<function=read_file><parameter=path>b.py</parameter></function>'
        )
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 2

    def test_xml_outil_inconnu_filtre(self, client, valid_tools):
        text = (
            '<function=rm_rf><parameter=path>/</parameter></function>'
            '<function=read_file><parameter=path>x.py</parameter></function>'
        )
        calls = client._parse_text_tool_calls(text, valid_tools)
        assert calls is not None and len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"


# ── JSON cassé : repair triple-quotes ──────────────────────────────────────────


class TestJsonRepair:
    def test_repair_triple_quote_basique(self):
        # Cas observé sur qwen2.5-coder avec docstrings Python
        broken = r'{"name":"write_file","arguments":{"path":"u.py","content":"def f():\n    """docstring"""\n    pass"}}'
        repaired = LLMClient._repair_json_quotes(broken)
        # Doit être parsable maintenant
        data = json.loads(repaired)
        assert data["name"] == "write_file"
        assert '"""docstring"""' in data["arguments"]["content"]

    def test_repair_no_op_si_valide(self):
        # JSON déjà valide → repair laisse le texte tel quel (ou équivalent)
        valid = '{"name":"read_file","arguments":{"path":"x.py"}}'
        repaired = LLMClient._repair_json_quotes(valid)
        assert json.loads(repaired) == json.loads(valid)

    def test_parse_json_casse_via_repair(self, client, valid_tools):
        """End-to-end : _parse_text_tool_calls doit gérer le JSON cassé via repair."""
        broken = r'{"name":"write_file","arguments":{"path":"u.py","content":"def f():\n    """doc"""\n    return 1"}}'
        calls = client._parse_text_tool_calls(broken, valid_tools)
        assert calls is not None and len(calls) == 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert '"""doc"""' in args["content"]


# ── Format mixte (texte + appel) ───────────────────────────────────────────────


class TestExtractMixed:
    def test_mixte_texte_puis_json(self, client, valid_tools):
        content = 'Je vais sauvegarder.\n\n{"name":"write_file","arguments":{"path":"x.py","content":"a"}}'
        text_part, calls = client.extract_mixed_tool_call(content, valid_tools)
        assert calls is not None and len(calls) == 1
        assert text_part.strip().startswith("Je vais")
        assert calls[0]["function"]["name"] == "write_file"

    def test_mixte_texte_puis_xml(self, client, valid_tools):
        content = (
            'Je vais lire le fichier.\n\n'
            '<function=read_file><parameter=path>script.py</parameter></function>'
        )
        text_part, calls = client.extract_mixed_tool_call(content, valid_tools)
        assert calls is not None and len(calls) == 1
        assert calls[0]["function"]["name"] == "read_file"
        assert text_part.startswith("Je vais lire")

    def test_pas_d_appel_retourne_contenu_intact(self, client, valid_tools):
        content = "Voici une explication sans appel d'outil."
        text_part, calls = client.extract_mixed_tool_call(content, valid_tools)
        assert calls is None
        assert text_part == content

    def test_pure_json_via_extract(self, client, valid_tools):
        """Si le contenu est du JSON pur, retourne texte vide + appel."""
        content = '{"name":"read_file","arguments":{"path":"x.py"}}'
        text_part, calls = client.extract_mixed_tool_call(content, valid_tools)
        assert calls is not None and len(calls) == 1
        assert text_part == ""

    def test_pure_xml_via_extract(self, client, valid_tools):
        content = '<function=read_file><parameter=path>x.py</parameter></function>'
        text_part, calls = client.extract_mixed_tool_call(content, valid_tools)
        assert calls is not None and len(calls) == 1
        assert text_part == ""


# ── Backend switching ─────────────────────────────────────────────────────────


class TestBackendSwitch:
    def test_ollama_par_defaut(self, monkeypatch):
        """BACKEND=ollama → LLM_BASE_URL = OLLAMA_BASE_URL."""
        monkeypatch.setenv("BACKEND", "ollama")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://test-ollama:11434/v1")
        monkeypatch.setenv("MLX_BASE_URL", "http://test-mlx:8080/v1")
        import importlib
        import config
        importlib.reload(config)
        assert config.LLM_BASE_URL == "http://test-ollama:11434/v1"
        assert config.BACKEND == "ollama"

    def test_mlx_actif(self, monkeypatch):
        """BACKEND=mlx → LLM_BASE_URL = MLX_BASE_URL."""
        monkeypatch.setenv("BACKEND", "mlx")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://test-ollama:11434/v1")
        monkeypatch.setenv("MLX_BASE_URL", "http://test-mlx:8080/v1")
        import importlib
        import config
        importlib.reload(config)
        assert config.LLM_BASE_URL == "http://test-mlx:8080/v1"
        assert config.BACKEND == "mlx"
        assert "Qwen3" in config.LLM_MODEL or "qwen3" in config.LLM_MODEL.lower()

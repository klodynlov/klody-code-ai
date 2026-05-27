"""Tests pour le text-to-action fallback (orchestrator).

Quand le LLM produit du code dans des blocs ```lang``` au lieu d'appeler
les tools natifs, on extrait et on invoque le bon tool nous-mêmes.
"""
from __future__ import annotations

import json

from agent.orchestrator import _extract_code_blocks, _infer_action_from_text


class TestExtractCodeBlocks:
    def test_un_bloc_html(self):
        c = "Voici le code :\n```html\n<canvas id='c'></canvas>\n```\nC'est fait."
        blocks = _extract_code_blocks(c)
        assert "html" in blocks
        assert blocks["html"][0] == "<canvas id='c'></canvas>"

    def test_multi_blocs(self):
        c = "```html\n<div></div>\n```\n```js\nconsole.log(1)\n```"
        blocks = _extract_code_blocks(c)
        assert blocks["html"] == ["<div></div>"]
        assert blocks["js"] == ["console.log(1)"]

    def test_alias_javascript_normalise_js(self):
        c = "```javascript\nlet x = 1;\n```"
        blocks = _extract_code_blocks(c)
        assert "js" in blocks
        assert "javascript" not in blocks

    def test_alias_py_normalise_python(self):
        c = "```py\nprint(1)\n```"
        blocks = _extract_code_blocks(c)
        assert "python" in blocks

    def test_bloc_vide_skip(self):
        c = "```html\n\n```"
        blocks = _extract_code_blocks(c)
        assert blocks == {}

    def test_sans_bloc(self):
        assert _extract_code_blocks("juste du texte") == {}


class TestInferAction:
    def test_web_html_js_avec_three(self):
        c = (
            "Voici la maison 3D :\n"
            "```html\n<canvas id='c' width='800' height='600'></canvas>\n```\n"
            "```js\nconst scene = new THREE.Scene();\nrenderer.render(scene, camera);\n```"
        )
        action = _infer_action_from_text(c, "cree moi une maison en 3D")
        assert action is not None
        assert action["name"] == "preview_code"
        args = action["args"]
        assert "<canvas" in args["html"]
        assert "THREE.Scene" in args["js"]
        # Three.js CDN auto-injecté
        assert "scripts" in args
        assert any("three" in s.lower() for s in args["scripts"])

    def test_web_chart_js_detecte(self):
        c = "```html\n<canvas></canvas>\n```\n```js\nnew Chart(ctx, {});\n```"
        action = _infer_action_from_text(c, "graphique")
        assert action["name"] == "preview_code"
        assert any("chart" in s.lower() for s in action["args"]["scripts"])

    def test_python_seul(self):
        c = "Voici le script :\n```python\ndef hello(): print('hi')\nhello()\n```"
        action = _infer_action_from_text(c, "écris un script")
        assert action["name"] == "write_file"
        assert action["args"]["path"] == "script.py"
        assert "hello" in action["args"]["content"]

    def test_bash(self):
        c = "```bash\nls -la\n```"
        action = _infer_action_from_text(c, "liste fichiers")
        assert action["name"] == "execute_command"
        assert "ls -la" in action["args"]["command"]

    def test_rien_extractable(self):
        assert _infer_action_from_text("juste un plan en texte", "x") is None
        assert _infer_action_from_text("", "x") is None

    def test_priorite_web_sur_python(self):
        """HTML+JS détecté → preview_code même s'il y a aussi du Python."""
        c = "```html\n<div></div>\n```\n```python\nprint(1)\n```"
        action = _infer_action_from_text(c, "x")
        assert action["name"] == "preview_code"

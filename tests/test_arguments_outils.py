"""Les `arguments` d'un tool_call sont TOUJOURS un objet JSON dans l'historique.

`mlx_lm.server` (0.31.3) fait `json.loads` sur les arguments de chaque message
passé (`mlx_lm/server.py:150`). Une seule entrée invalide — JSON tronqué par le
modèle, ou `str(args)` (repr Python) du repli texte — faisait échouer toutes les
requêtes suivantes de la session. Et un JSON valide mais non-objet faisait lever
`AttributeError` sur `tool_args.items()` (audit du 2026-09-27).
"""

from __future__ import annotations

import json

import pytest
from agent.arguments_outils import arguments_dict, arguments_json


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ('{"path": "a.py"}', {"path": "a.py"}),
        ({"path": "a.py"}, {"path": "a.py"}),
        ('"{\\"path\\": \\"a.py\\"}"', {"path": "a.py"}),  # doublement encodé
        ('{"path": "a.py"', {}),  # tronqué
        ('"app.py"', {}),  # chaîne JSON, pas un objet
        ("app.py", {}),  # pas du JSON
        ('["a", "b"]', {}),
        ("42", {}),
        (None, {}),
        (["a"], {}),
    ],
)
def test_arguments_dict(brut, attendu):
    assert arguments_dict(brut) == attendu


@pytest.mark.parametrize(
    "brut", ['{"path": "a.py"', "app.py", "['a']", ["a"], None, 3, '"x"', {"k": "é"}]
)
def test_arguments_json_est_toujours_un_objet_relisible(brut):
    """La propriété exacte dont dépend mlx_lm : json.loads rend un dict."""
    assert isinstance(json.loads(arguments_json(brut)), dict)


def test_un_objet_valide_passe_octet_pour_octet():
    brut = '{"path":  "a.py"}'
    assert arguments_json(brut) is brut


@pytest.mark.parametrize(
    "texte",
    [
        '{"name": "read_file", "arguments": "app.py"}',
        '{"name": "read_file", "arguments": ["app.py"]}',
        '[{"name": "read_file", "arguments": 3}]',
    ],
)
def test_le_repli_texte_du_client_n_emet_plus_de_repr_python(texte):
    """`agent/llm.py` produisait `str(args)` pour des arguments non-dict :
    `'app.py'`, `"['app.py']"` — illisibles pour mlx_lm au tour suivant."""
    from agent.llm import LLMClient

    client = LLMClient.__new__(LLMClient)
    calls = client._parse_text_tool_calls(texte, {"read_file"})
    assert calls, "le repli doit toujours reconnaître l'appel"
    for tc in calls:
        assert isinstance(json.loads(tc["function"]["arguments"]), dict)

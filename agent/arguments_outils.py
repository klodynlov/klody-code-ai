"""Arguments de tool_call : toujours un OBJET JSON, du modèle jusqu'à l'historique.

Deux pannes, une même cause — des `arguments` qui ne sont pas un objet JSON :

1. `json.loads` réussit mais rend une liste, une chaîne, un nombre : le tour
   levait `AttributeError` sur `tool_args.items()` (audit du 2026-09-27).
2. Pire, et silencieux : l'historique gardait la chaîne telle quelle — un JSON
   TRONQUÉ par le modèle, ou `str(args)` (repr Python) dans le repli texte de
   `agent/llm.py`. Or `mlx_lm.server` fait `json.loads` sur les `arguments` de
   CHAQUE message passé (`mlx_lm/server.py:150`, 0.31.3) : une seule entrée
   invalide faisait échouer toutes les requêtes suivantes de la session.

Règle : un objet JSON valide passe tel quel (octet pour octet, rien à gagner à
le re-sérialiser) ; un objet doublement encodé est décodé ; tout le reste
devient `{}` — l'outil répondra « argument manquant » et le modèle corrigera,
ce qui vaut mieux qu'une session morte.
"""

from __future__ import annotations

import json
from typing import Any


def arguments_dict(brut: Any) -> dict[str, Any]:
    """Les arguments d'un tool_call sous forme de dict, quoi que le modèle ait émis."""
    valeur = brut
    # Deux passes au plus : un objet encodé dans une chaîne JSON est courant.
    for _ in range(2):
        if isinstance(valeur, dict):
            return valeur
        if not isinstance(valeur, str):
            return {}
        try:
            valeur = json.loads(valeur)
        except (json.JSONDecodeError, ValueError):
            return {}
    return valeur if isinstance(valeur, dict) else {}


def arguments_json(brut: Any) -> str:
    """Chaîne JSON d'un OBJET, sûre à renvoyer au serveur dans l'historique."""
    if isinstance(brut, str):
        try:
            if isinstance(json.loads(brut), dict):
                return brut
        except (json.JSONDecodeError, ValueError):
            pass
    return json.dumps(arguments_dict(brut), ensure_ascii=False)

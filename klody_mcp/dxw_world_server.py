"""Lanceur stdio du serveur MCP « dream-x-world » pour KlodyAI.

Câble le moteur de mondes Dream × World (paquet `dreamworld`) sur la stack
locale Klody, sans dépendre de l'environnement du process parent :

  - Génération  -> gateway Klody Core (:8090, modèle « brain »)  [DXW_LLM_PROVIDER=gateway]
  - Embeddings  -> sentence-transformers BAAI/bge-m3 (1024d)     [DXW_EMBED_PROVIDER=st]
  - Monde       -> base SQLite dédiée sous data/ (canon persistant)

Cible déclarée dans KLODY_MCP_SERVERS (chemin de ce script). fastmcp.Client le
lance via stdio ; les 6 outils (world_seed/expand/query/advance_time/get_entity/
list) deviennent mcp__dream-x-world__*.

NB : ce n'est PAS `dreamx_server.py` (= world model VIDÉO). Projet distinct.
"""

import os
from pathlib import Path

_DB = Path(__file__).resolve().parent.parent / "data" / "dreamworld.db"

_DEFAULTS = {
    "DXW_LLM_PROVIDER": "gateway",
    "DXW_GATEWAY_URL": "http://127.0.0.1:8090/v1",
    "DXW_GATEWAY_MODEL": "brain",
    "DXW_EMBED_PROVIDER": "st",
    "DXW_ST_MODEL": "BAAI/bge-m3",
    "DXW_EMBED_DIM": "1024",
    "DXW_DB": str(_DB),
    # Chemin rapide : le pont MCP de Klody coupe les appels à 60 s et relance un
    # process par appel. On limite les allers-retours au modèle brain :
    "DXW_BEST_OF": "1",      # pas de Best-of-N (1 génération par expand)
    "DXW_USE_JUDGE": "0",    # cohérence = invariants structurels seuls (pas de juge LLM)
    "DXW_MAX_TOKENS": "4096",
    "DXW_TEMP": "0.6",
    # bge-m3 est déjà en cache : pas de round-trip réseau HF à chaque démarrage.
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}
for _k, _v in _DEFAULTS.items():
    os.environ.setdefault(_k, _v)

_DB.parent.mkdir(parents=True, exist_ok=True)

# Import APRÈS avoir posé l'env (EMBED_DIM est lu à l'import de dreamworld.models).
from dreamworld.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()

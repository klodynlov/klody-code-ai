"""Route /health commune pour les serveurs MCP FastMCP.

Incident du 2026-08-05 : les 8 serveurs MCP partagent le venv et le mode de
panne « dépendances périmées » — pip réécrit site-packages SOUS un process
vivant. Le diagnostic externe (`diagnostic_peremption.py`) les voit ; eux-mêmes
ne disaient rien. Ce module leur donne la parole.

Trois verdicts, jamais deux :
  - `a_jour`   → 200
  - `perimees` → 503 + remède nommé (launchctl kickstart)
  - `non_juge` → 503 (ne JAMAIS rendre 200 sur non_juge)

Usage dans chaque serveur MCP :
    from klody_mcp.health import register_health_route
    register_health_route(mcp, "com.klody.reaper-mcp")
    mcp.run(...)
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_CONTENT_TYPE = "application/json; charset=utf-8"


def _health_response(label_service: str) -> tuple[dict, int]:
    """Calcule le verdict et le code HTTP.

    Retourne (body_dict, status_code). Isolé du transport pour être testable
    sans Starlette.
    """
    from agent.peremption import A_JOUR, etat_process

    etat = etat_process(label_service=label_service)
    status_code = 200 if etat["statut"] == A_JOUR else 503
    return etat, status_code


def register_health_route(mcp: FastMCP, label_service: str) -> None:
    """Enregistre GET /health sur un serveur FastMCP via custom_route.

    La route n'est PAS un outil MCP — elle ne modifie pas le snapshot de contrat.
    """
    from starlette.requests import Request
    from starlette.responses import Response

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        try:
            body, code = _health_response(label_service)
        except Exception:
            logger.exception("health check échoué")
            body = {
                "statut": "non_juge",
                "raison": "erreur interne lors du health check",
                "remede": None,
            }
            code = 503

        return Response(
            content=json.dumps(body, ensure_ascii=False),
            status_code=code,
            media_type=_CONTENT_TYPE,
        )

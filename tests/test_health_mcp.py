"""Tests pour klody_mcp.health — route /health des serveurs MCP.

Vérifie le contrat à trois verdicts (a_jour/perimees/non_juge),
le mapping code HTTP, la gestion d'exception, et le câblage des 9 serveurs.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from klody_mcp.health import _health_response

_PATCH_ETAT = "agent.peremption.etat_process"

# ── Verdicts → codes HTTP ───────────────────────────────────────────────────

_LABEL = "com.klody.test-mcp"


def _fake_etat(statut: str, raison: str = "test", remede: str | None = None) -> dict:
    return {"statut": statut, "raison": raison, "remede": remede}


class TestHealthResponse:
    """_health_response retourne (body, code) selon le verdict peremption."""

    def test_a_jour_rend_200(self):
        with patch(_PATCH_ETAT, return_value=_fake_etat("a_jour")):
            body, code = _health_response(_LABEL)
        assert code == 200
        assert body["statut"] == "a_jour"

    def test_perimees_rend_503(self):
        etat = _fake_etat("perimees", remede="launchctl kickstart ...")
        with patch(_PATCH_ETAT, return_value=etat):
            body, code = _health_response(_LABEL)
        assert code == 503
        assert body["statut"] == "perimees"
        assert body["remede"] is not None

    def test_non_juge_rend_503(self):
        with patch(_PATCH_ETAT, return_value=_fake_etat("non_juge")):
            body, code = _health_response(_LABEL)
        assert code == 503
        assert body["statut"] == "non_juge"

    def test_label_transmis(self):
        with patch(_PATCH_ETAT, return_value=_fake_etat("a_jour")) as m:
            _health_response("com.klody.reaper-mcp")
        m.assert_called_once_with(label_service="com.klody.reaper-mcp")


# ── Exception → non_juge + 503 ─────────────────────────────────────────────


class TestHealthException:
    """Une exception dans etat_process ne doit JAMAIS rendre 200."""

    def test_exception_rend_non_juge_503(self):
        from klody_mcp.health import register_health_route
        from starlette.testclient import TestClient

        try:
            from fastmcp import FastMCP
        except ImportError:
            pytest.skip("fastmcp non installé")

        app = FastMCP("test-health")
        register_health_route(app, _LABEL)

        with patch(
            _PATCH_ETAT,
            side_effect=RuntimeError("site-packages introuvable"),
        ):
            client = TestClient(app.http_app())
            resp = client.get("/health")

        assert resp.status_code == 503
        body = resp.json()
        assert body["statut"] == "non_juge"
        assert body["remede"] is None


# ── Câblage des 9 serveurs ──────────────────────────────────────────────────

_SERVEURS_MCP = [
    "gadget_server",
    "gmail_server",
    "klody_music_server",
    "klody_server",
    "memory_server",
    "reaper_server",
    "vlc_server",
    "vocalbrain_server",
    "web_server",
]


class TestCablage:
    """Chaque serveur MCP importe et appelle register_health_route."""

    @pytest.mark.parametrize("serveur", _SERVEURS_MCP)
    def test_import_present(self, serveur: str):
        source = Path(f"klody_mcp/{serveur}.py").read_text()
        assert "from klody_mcp.health import register_health_route" in source

    @pytest.mark.parametrize("serveur", _SERVEURS_MCP)
    def test_appel_present(self, serveur: str):
        source = Path(f"klody_mcp/{serveur}.py").read_text()
        tree = ast.parse(source)
        appels = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "register_health_route"
        ]
        assert len(appels) == 1, f"{serveur} doit appeler register_health_route exactement 1×"


# ── Contrat JSON ────────────────────────────────────────────────────────────


class TestContratJSON:
    """Le body doit contenir exactement statut, raison, remede."""

    @pytest.mark.parametrize("statut", ["a_jour", "perimees", "non_juge"])
    def test_trois_cles(self, statut: str):
        with patch(_PATCH_ETAT, return_value=_fake_etat(statut)):
            body, _ = _health_response(_LABEL)
        assert set(body.keys()) >= {"statut", "raison", "remede"}

    def test_json_serialisable(self):
        with patch(_PATCH_ETAT, return_value=_fake_etat("a_jour")):
            body, _ = _health_response(_LABEL)
        json.dumps(body, ensure_ascii=False)

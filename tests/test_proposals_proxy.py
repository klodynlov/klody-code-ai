"""Tests du relais /api/proposals* (brique 3 assistant proactif) — hermétiques.

On appelle les coroutines directement avec un httpx.AsyncClient mocké : zéro
gateway, zéro réseau. Couvre : liste nominale, best-effort gateway down (GET
= file vide 200, POST = 502 franc), relais des codes d'erreur gateway
(409 conservé), corps invalide, et l'en-tête X-Klody-App posé sur le POST.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import api.server as srv
import httpx
import pytest
from fastapi import HTTPException


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


class _FakeAsyncClient:
    """Remplace httpx.AsyncClient : rejoue une réponse (ou lève) et capture l'appel."""
    last: ClassVar[dict] = {}

    def __init__(self, response=None, exc=None):
        self._response, self._exc = response, exc

    def __call__(self, *a, **k):          # httpx.AsyncClient(timeout=…) → instance
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        _FakeAsyncClient.last = {"method": "GET", "url": url, **kw}
        if self._exc:
            raise self._exc
        return self._response

    async def post(self, url, **kw):
        _FakeAsyncClient.last = {"method": "POST", "url": url, **kw}
        if self._exc:
            raise self._exc
        return self._response


def _patch_client(monkeypatch, response=None, exc=None):
    monkeypatch.setattr(srv.httpx, "AsyncClient", _FakeAsyncClient(response, exc))


class _FakeRequest:
    def __init__(self, body=None, exc=None):
        self._body, self._exc = body, exc

    async def json(self):
        if self._exc:
            raise self._exc
        return self._body


class TestList:
    def test_nominal(self, monkeypatch):
        payload = {"proposals": [{"id": 1, "title": "Distiller zouk"}]}
        _patch_client(monkeypatch, _FakeResponse(payload))
        out = asyncio.run(srv.proposals_list())
        assert out == payload
        assert _FakeAsyncClient.last["url"].endswith("/proposals")
        assert _FakeAsyncClient.last["params"] == {"status": "new", "limit": 3}

    def test_gateway_down_file_vide(self, monkeypatch):
        _patch_client(monkeypatch, exc=httpx.ConnectError("down"))
        out = asyncio.run(srv.proposals_list())
        assert out["proposals"] == [] and "note" in out

    def test_erreur_gateway_file_vide(self, monkeypatch):
        _patch_client(monkeypatch, _FakeResponse({"error": "x"}, status_code=400))
        assert asyncio.run(srv.proposals_list())["proposals"] == []


class TestSetStatus:
    def test_nominal_relaye_et_entete(self, monkeypatch):
        _patch_client(monkeypatch, _FakeResponse({"ok": True, "status": "accepted"}))
        out = asyncio.run(srv.proposals_set_status(
            1, _FakeRequest({"status": "accepted"})))
        assert out["ok"] is True
        assert _FakeAsyncClient.last["headers"] == {"X-Klody-App": "klody-ui"}
        assert _FakeAsyncClient.last["json"] == {"status": "accepted"}
        assert _FakeAsyncClient.last["url"].endswith("/proposals/1/status")

    def test_code_gateway_conserve(self, monkeypatch):
        _patch_client(monkeypatch, _FakeResponse(
            {"error": "transition refusée"}, status_code=409))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(srv.proposals_set_status(5, _FakeRequest({"status": "accepted"})))
        assert ei.value.status_code == 409
        assert "refusée" in ei.value.detail

    def test_gateway_down_502(self, monkeypatch):
        _patch_client(monkeypatch, exc=httpx.ConnectError("down"))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(srv.proposals_set_status(1, _FakeRequest({"status": "shown"})))
        assert ei.value.status_code == 502

    def test_corps_invalide_400(self, monkeypatch):
        _patch_client(monkeypatch, _FakeResponse({"ok": True}))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(srv.proposals_set_status(1, _FakeRequest(exc=ValueError("bad"))))
        assert ei.value.status_code == 400

    def test_erreur_gateway_corps_non_json(self, monkeypatch):
        _patch_client(monkeypatch, _FakeResponse(ValueError("pas du JSON"), status_code=500))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(srv.proposals_set_status(1, _FakeRequest({"status": "shown"})))
        assert ei.value.status_code == 500 and ei.value.detail == "échec gateway"


class TestGatewayRoot:
    def test_derive_de_mlx_base_url(self, monkeypatch):
        monkeypatch.setattr(srv.config, "MLX_BASE_URL", "http://localhost:8090/v1")
        assert srv._gateway_root() == "http://localhost:8090"

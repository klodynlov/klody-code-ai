"""Tests klody_mcp.samplebrain_server — serveur SampleBrain mocké (_request)."""
from __future__ import annotations

import pytest
from klody_mcp import samplebrain_server as sb


def test_chercher_samples_formate_les_hits(monkeypatch):
    def fake_request(path, params=None):
        assert path == "/api/search"
        assert params == {"q": "warm rhodes", "k": 2}
        return {"hits": [
            {"content_hash": "aaa", "distance": 0.8696,
             "paths": ["/S/CHORDS/Strum.wav", "/S/MIDI/Strum.wav"]},
            {"content_hash": "bbb", "distance": 0.9241, "paths": []},
        ]}

    monkeypatch.setattr(sb, "_request", fake_request)
    out = sb.chercher_samples("warm rhodes", k=2)
    assert out["requete"] == "warm rhodes"
    assert len(out["resultats"]) == 1  # le hit sans chemin est ignoré
    hit = out["resultats"][0]
    assert hit["fichier"] == "Strum.wav"
    assert hit["chemin"] == "/S/CHORDS/Strum.wav"
    assert hit["autres_chemins"] == ["/S/MIDI/Strum.wav"]
    assert hit["distance"] == 0.8696


def test_chercher_samples_borne_k(monkeypatch):
    captured = {}

    def fake_request(path, params=None):
        captured.update(params)
        return {"hits": []}

    monkeypatch.setattr(sb, "_request", fake_request)
    sb.chercher_samples("kick", k=999)
    assert captured["k"] == 50
    sb.chercher_samples("kick", k=-3)
    assert captured["k"] == 1


def test_description_vide_ne_touche_pas_le_reseau(monkeypatch):
    def boom(path, params=None):  # pragma: no cover
        raise AssertionError("ne doit pas être appelé")

    monkeypatch.setattr(sb, "_request", boom)
    assert sb.chercher_samples("   ") == {"erreur": "description vide"}


def test_serveur_absent_message_actionnable(monkeypatch):
    import re

    import httpx

    def down(url, params=None, timeout=None):
        raise httpx.ConnectError("refus")

    monkeypatch.setattr(sb.httpx, "get", down)
    with pytest.raises(RuntimeError, match=re.escape("samplebrain.indexer.cli serve")):
        sb.statut_index()

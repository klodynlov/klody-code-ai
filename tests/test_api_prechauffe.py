"""Le démarrage de l'API préchauffe le modèle d'embedding, sans l'attendre.

Mesuré le 2026-11-19 : avec l'index de code persisté (PR #325), la première
recherche d'un process neuf restait à ~7-8 s, presque tout en chargement de
bge-m3. Le lifespan lance donc `tools.embeddings.prechauffer` dans un fil de fond.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def app_sans_effets(monkeypatch):
    monkeypatch.setattr("services.ensure_librarybrain", lambda *_a, **_kw: True)
    monkeypatch.setattr("tools.preview._ensure_server", lambda: None)
    from api.server import app

    return app


def test_le_demarrage_lance_le_prechauffage_en_fond(app_sans_effets, monkeypatch):
    from fastapi.testclient import TestClient
    from tools import embeddings

    appele = threading.Event()
    noms = []

    def faux():
        noms.append(threading.current_thread().name)
        appele.set()
        return True

    monkeypatch.setattr(embeddings, "prechauffer", faux)
    with TestClient(app_sans_effets, base_url="http://127.0.0.1:8000"):
        assert appele.wait(5)
    assert noms == ["embed-prechauffe"]


def test_le_demarrage_n_attend_pas_le_prechauffage(app_sans_effets, monkeypatch):
    """Un chargement lent (8,75 s en vrai) ne doit pas retarder l'écoute."""
    from fastapi.testclient import TestClient
    from tools import embeddings

    libere = threading.Event()
    monkeypatch.setattr(embeddings, "prechauffer", lambda: libere.wait(10))
    debut = time.monotonic()
    try:
        # Entrer dans le contexte = lifespan passé jusqu'au `yield` = l'API écoute.
        with TestClient(app_sans_effets, base_url="http://127.0.0.1:8000"):
            assert time.monotonic() - debut < 5
    finally:
        libere.set()

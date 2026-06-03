"""Ancrage de l'auteur sur LibraryBrain (`scripts.distill_book`).

Cause racine : quand l'utilisateur ne fournit pas d'auteur, le modèle en
*invente* un (« Ian Stanley », « Serge Kokot »…) au lieu de demander. On neutralise
ça en réécrivant `source.{book,author}` avec les métadonnées réelles renvoyées par
LibraryBrain (POST /api/ask → {found, sources:[{title, author, page, score}]}).

Tout est testé hors-ligne : les fonctions pures directement, la résolution réseau
avec httpx.Client mocké.
"""
from __future__ import annotations

import httpx
from scripts.distill_book import (
    _apply_grounded_source,
    _pick_source_from_sources,
    _resolve_source,
    _title_overlap,
)

REQ_TITLE = "Method Validation in Pharmaceutical Analysis - A Guide to Best Practice"
LB_TITLE = "Method Validation in Pharmaceutical Analysis"
REAL_AUTHOR = "Joachim Ermer"


# ── Fonctions pures ──────────────────────────────────────────────────────────

def test_title_overlap_is_subtitle_robust() -> None:
    assert _title_overlap("Method Validation", "method validation") == 1.0
    # Sous-titre en plus côté demande : le titre LibraryBrain (sous-ensemble) matche à fond.
    assert _title_overlap(
        "Method Validation in Pharmaceutical Analysis - A Guide to Best Practice",
        "Method Validation in Pharmaceutical Analysis",
    ) == 1.0
    assert _title_overlap("Deep Learning with Python", "Cooking for Geeks") == 0.0
    assert _title_overlap("", "anything") == 0.0


def test_pick_source_matches_best_title_and_returns_author() -> None:
    sources = [
        {"title": "Some Unrelated Statistics Book", "author": "Nobody", "score": 0.9},
        {"title": LB_TITLE, "author": REAL_AUTHOR, "page": 12, "score": 0.7},
    ]
    got = _pick_source_from_sources(REQ_TITLE, sources)
    assert got == {"book": LB_TITLE, "author": REAL_AUTHOR}


def test_pick_source_returns_none_when_no_strong_match() -> None:
    # Titre demandé sans recouvrement franc → on ne réécrit pas à l'aveugle.
    sources = [{"title": "Cooking for Geeks", "author": "Jeff Potter", "score": 0.9}]
    assert _pick_source_from_sources(REQ_TITLE, sources) is None


def test_pick_source_skips_sources_without_author() -> None:
    sources = [{"title": LB_TITLE, "author": "", "score": 0.9}]
    assert _pick_source_from_sources(REQ_TITLE, sources) is None


def test_apply_grounded_overrides_author_keeps_year() -> None:
    data = {"source": {"book": REQ_TITLE, "author": "Serge Kokot", "year": 2005}}
    out = _apply_grounded_source(data, {"book": LB_TITLE, "author": REAL_AUTHOR})
    assert out["source"]["author"] == REAL_AUTHOR
    assert out["source"]["book"] == LB_TITLE
    assert out["source"]["year"] == 2005  # préservé


def test_apply_grounded_is_noop_when_none() -> None:
    data = {"source": {"book": REQ_TITLE, "author": "Serge Kokot"}}
    out = _apply_grounded_source(data, None)
    assert out["source"]["author"] == "Serge Kokot"


# ── Résolution réseau (httpx mocké) ──────────────────────────────────────────

def _patch_librarybrain(monkeypatch, *, payload=None, exc=None) -> None:
    class _Resp:
        def raise_for_status(self) -> None: pass
        def json(self): return payload
    class _Client:
        def __init__(self, *a, **k) -> None: pass
        def __enter__(self): return self
        def __exit__(self, *a) -> bool: return False
        def post(self, *a, **k):
            if exc:
                raise exc
            return _Resp()
    monkeypatch.setattr("scripts.distill_book.httpx.Client", _Client)


def test_resolve_source_grounds_from_librarybrain(monkeypatch) -> None:
    _patch_librarybrain(monkeypatch, payload={
        "found": True,
        "sources": [{"title": LB_TITLE, "author": REAL_AUTHOR, "page": 3, "score": 0.8}],
    })
    assert _resolve_source(REQ_TITLE) == {"book": LB_TITLE, "author": REAL_AUTHOR}


def test_resolve_source_none_when_not_found(monkeypatch) -> None:
    _patch_librarybrain(monkeypatch, payload={"found": False, "sources": []})
    assert _resolve_source(REQ_TITLE) is None


def test_resolve_source_none_when_librarybrain_down(monkeypatch) -> None:
    _patch_librarybrain(monkeypatch, exc=httpx.ConnectError("connection refused"))
    assert _resolve_source(REQ_TITLE) is None  # dégradation gracieuse, pas de crash

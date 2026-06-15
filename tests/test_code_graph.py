"""Tests de l'outil code_graph (lecture du graphe graphify, lecture seule).

Construit un petit graph.json synthétique sous <tmp>/graphify-out/ et exerce
les 4 modes (overview/explain/callers/path) + les cas limites (graphe absent,
symbole inconnu, ambiguïté, nœuds vendored/rationale filtrés, dispatch query()).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools import code_graph


def _write_graph(root: Path) -> None:
    nodes = [
        {"id": "cls_orch", "label": "Orchestrator", "source_file": "agent/orchestrator.py", "source_location": "L50", "community": 1},
        {"id": "m_run", "label": ".run()", "source_file": "agent/orchestrator.py", "source_location": "L100", "community": 1},
        {"id": "m_disp", "label": "._execute_and_display()", "source_file": "agent/orchestrator.py", "source_location": "L200", "community": 1},
        {"id": "m_exec", "label": "._execute_tool()", "source_file": "agent/orchestrator.py", "source_location": "L300", "community": 1},
        # vendored → doit être exclu du classement overview
        {"id": "vendor_copy", "label": ".copy()", "source_file": "_preview/vendor/three/three.module.js", "source_location": "L9", "community": 2},
        # rationale (docstring) → ignoré par _match et overview
        {"id": "agent_orchestrator_rationale_99", "label": "doc", "source_file": "agent/orchestrator.py", "source_location": "L99", "community": 1},
        # deux mêmes labels → ambiguïté
        {"id": "dup1", "label": "helper()", "source_file": "a.py", "source_location": "L1", "community": 3},
        {"id": "dup2", "label": "helper()", "source_file": "b.py", "source_location": "L2", "community": 3},
        # isolé → pas de chemin
        {"id": "iso", "label": "lonely()", "source_file": "c.py", "source_location": "L1", "community": 4},
    ]
    links = [
        {"source": "cls_orch", "target": "m_run", "relation": "method", "confidence": "EXTRACTED"},
        {"source": "cls_orch", "target": "m_exec", "relation": "method", "confidence": "EXTRACTED"},
        {"source": "cls_orch", "target": "vendor_copy", "relation": "calls", "confidence": "EXTRACTED"},
        {"source": "m_run", "target": "m_disp", "relation": "calls", "confidence": "EXTRACTED"},
        {"source": "m_disp", "target": "m_exec", "relation": "calls", "confidence": "EXTRACTED"},
    ]
    out = root / "graphify-out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text(json.dumps({
        "directed": False, "multigraph": False, "graph": {},
        "nodes": nodes, "links": links, "hyperedges": [],
        "built_at_commit": "deadbeefcafe",
    }), encoding="utf-8")


@pytest.fixture()
def graph_root(tmp_path: Path) -> Path:
    _write_graph(tmp_path)
    return tmp_path


# -- graphe absent ---------------------------------------------------------- #

def test_absent_returns_hint(tmp_path: Path):
    assert "introuvable" in code_graph.query(tmp_path, {"mode": "overview"})
    assert code_graph.explain(tmp_path, "x").startswith("Graphe code absent")


# -- overview --------------------------------------------------------------- #

def test_overview_ranks_and_filters_vendored(graph_root: Path):
    out = code_graph.overview(graph_root)
    assert "deadbeef" in out
    assert "Orchestrator" in out               # god node #1 (degré 3)
    assert "three.module.js" not in out        # vendored exclu
    assert "doc" not in out                    # rationale exclu
    # 7 symboles non-vendored/non-rationale (9 - vendor - rationale)
    assert "7 symboles" in out


# -- explain ---------------------------------------------------------------- #

def test_explain_shows_typed_neighbors(graph_root: Path):
    out = code_graph.explain(graph_root, "_execute_tool")
    assert "._execute_tool()" in out
    assert "agent/orchestrator.py:L300" in out
    assert "Appelé / contenu par (2)" in out   # cls_orch (method) + m_disp (calls)
    assert "Orchestrator" in out and "._execute_and_display()" in out


def test_explain_outgoing(graph_root: Path):
    out = code_graph.explain(graph_root, "run")
    assert "Appelle / contient (1)" in out
    assert "._execute_and_display()" in out


def test_explain_not_found(graph_root: Path):
    assert "Aucun nœud" in code_graph.explain(graph_root, "n_existe_pas")


def test_explain_ambiguous(graph_root: Path):
    out = code_graph.explain(graph_root, "helper")
    assert "ambigu" in out
    assert "a.py:L1" in out and "b.py:L2" in out


# -- callers ---------------------------------------------------------------- #

def test_callers_named(graph_root: Path):
    out = code_graph.callers(graph_root, "_execute_tool")
    assert "Appelants de" in out
    assert "Orchestrator" in out
    assert "agent/orchestrator.py:L200" in out  # _execute_and_display location


def test_callers_none(graph_root: Path):
    # cls_orch n'a aucune arête entrante
    assert "aucun appelant" in code_graph.callers(graph_root, "Orchestrator")


def test_callers_not_found(graph_root: Path):
    assert "Aucun nœud" in code_graph.callers(graph_root, "zzz")


# -- path ------------------------------------------------------------------- #

def test_path_found(graph_root: Path):
    out = code_graph.path(graph_root, "run", "_execute_tool")
    assert "2 sauts" in out
    assert ".run()" in out and "._execute_tool()" in out
    assert "--calls-->" in out


def test_path_none(graph_root: Path):
    assert "Pas de chemin" in code_graph.path(graph_root, "run", "lonely")


def test_path_missing_symbol(graph_root: Path):
    assert "Aucun nœud `nope`" in code_graph.path(graph_root, "nope", "run")
    assert "Aucun nœud `nada`" in code_graph.path(graph_root, "run", "nada")


# -- dispatch query() ------------------------------------------------------- #

def test_query_modes(graph_root: Path):
    assert "God nodes" in code_graph.query(graph_root, {"mode": "overview"})
    assert "Appelants" in code_graph.query(graph_root, {"mode": "callers", "symbol": "_execute_tool"})
    assert "sauts" in code_graph.query(graph_root, {"mode": "path", "symbol": "run", "to": "_execute_tool"})
    # défaut = explain
    assert "Nœud" in code_graph.query(graph_root, {"symbol": "run"})


def test_query_missing_args(graph_root: Path):
    assert "symbol` ET `to`" in code_graph.query(graph_root, {"mode": "path", "symbol": "run"})
    assert "fournis `symbol`" in code_graph.query(graph_root, {"mode": "callers"})


def test_cache_reload(graph_root: Path):
    # deux appels successifs : 2e via cache (même mtime), résultat identique
    a = code_graph.overview(graph_root)
    b = code_graph.overview(graph_root)
    assert a == b

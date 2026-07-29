"""Tests des adaptateurs `_tool_*` de l'orchestrator.

Ces méthodes ont l'air de simples passe-plats vers `tools/`, et la plupart le
sont. Mais quatre d'entre elles portent la logique qui a justifié des correctifs
récents, et n'étaient couvertes par rien :

- la **validation de chemin** avant d'exécuter ou d'analyser (`run_in_sandbox`,
  `analyze_dependencies`) — un workdir hors sandbox doit être refusé AVANT
  l'appel à l'outil ;
- la **qualification des absences** (`find_symbol`, `find_references`,
  `find_relevant_files`) — un index vide ou en panne ne doit jamais se lire
  « ce symbole n'existe pas », c'est l'objet des correctifs #158-#160 ;
- la **coercition d'arguments** venus du LLM (`run_sql`), qui n'a aucune garantie
  de type.

Montage minimal : `Orchestrator.__new__` avec les seuls collaborateurs utilisés,
comme tests/test_sandbox_multiroot.py.
"""
from __future__ import annotations

import pytest
from agent.orchestrator import Orchestrator
from tools.file_manager import FileManager


class _FauxIndex:
    """Index de code dont on pilote les retours et le motif d'absence."""

    def __init__(self, symbols=None, refs=None, miss=None, indexed=0):
        self._symbols = symbols or []
        self._refs = refs or []
        self.last_miss = miss
        self._indexed = indexed

    def find_symbol(self, name):
        return self._symbols

    def find_references(self, name):
        return self._refs

    def indexed_count(self):
        return self._indexed


class _FauxEmbedIndex:
    def __init__(self, hits=None, miss=None):
        self._hits = hits or []
        self.last_miss = miss

    def search(self, query, k=5):
        self.dernier_k = k
        return self._hits


@pytest.fixture
def orch(tmp_path):
    """Orchestrator minimal, sandbox confiné à tmp_path."""
    o = Orchestrator.__new__(Orchestrator)
    o.file_manager = FileManager(root=tmp_path, allowed_roots=[tmp_path])
    o._sandbox_cache = {}
    o._sandbox_timeout = 20
    o._sandbox_auto_exec = False
    # `code_index` et `embed_index` sont des propriétés LAZY sans setter : on
    # pré-remplit leur cache pour éviter la construction du vrai index.
    o._code_index = None
    o._embed_index = None
    return o


# --- validation de chemin : refuser AVANT d'exécuter ------------------------


def test_run_in_sandbox_refuse_un_workdir_hors_racines(orch, tmp_path):
    """Le refus doit précéder l'exécution : une commande ne part jamais dans un
    répertoire non autorisé."""
    out = orch._tool_run_in_sandbox({"command": "echo bonjour", "workdir": "/etc"})

    assert out.startswith("ERREUR SÉCURITÉ")
    assert "/etc" in out


def test_analyze_dependencies_refuse_un_chemin_hors_racines(orch):
    out = orch._tool_analyze_dependencies({"path": "/etc"})

    assert out.startswith("ERREUR SÉCURITÉ")
    assert "hors des racines autorisées" in out


def test_analyze_dependencies_accepte_un_chemin_relatif_sous_la_racine(orch, tmp_path):
    """Un relatif se résout contre la racine du sandbox, pas contre le cwd."""
    (tmp_path / "sous").mkdir()
    out = orch._tool_analyze_dependencies({"path": "sous"})

    assert not out.startswith("ERREUR SÉCURITÉ")


@pytest.mark.parametrize("chemin", ["", "   ", None])
def test_analyze_dependencies_defaut_sur_la_racine(orch, chemin):
    """Chemin absent ou vide → la racine, jamais une exception."""
    out = orch._tool_analyze_dependencies({"path": chemin})

    assert not out.startswith("ERREUR SÉCURITÉ")


# --- qualification des absences (correctifs #158-#160) ----------------------


def test_find_symbol_nomme_la_panne_au_lieu_de_nier(orch):
    """Zéro résultat avec un index à 0 symbole n'est PAS « ce symbole n'existe
    pas » : c'est indiscernable d'une indexation qui n'a pas tourné."""
    orch._code_index = _FauxIndex(symbols=[], miss="tree-sitter absent", indexed=0)

    out = orch._tool_find_symbol({"name": "MaClasse"})

    assert "MaClasse" in out
    assert "tree-sitter absent" in out          # la cause est nommée
    assert "PAS un verdict" in out              # et l'absence explicitement récusée


def test_find_symbol_rend_les_symboles_trouves(orch):
    from tools.code_index import Symbol

    orch._code_index = _FauxIndex(
        symbols=[Symbol(name="MaClasse", kind="class", file="a.py", line=3)],
        indexed=12,
    )

    assert "MaClasse" in orch._tool_find_symbol({"name": "MaClasse"})


def test_find_references_nomme_la_panne_au_lieu_de_nier(orch):
    orch._code_index = _FauxIndex(refs=[], miss="index vide", indexed=0)

    out = orch._tool_find_references({"name": "ma_fonction"})

    assert "ma_fonction" in out
    assert "index vide" in out
    assert "PAS un verdict" in out


def test_find_relevant_files_nomme_la_panne_au_lieu_de_nier(orch):
    """Zéro hit est TOUJOURS une panne ici : `search()` n'a pas de seuil de
    pertinence, donc « aucun fichier pertinent » serait un contresens."""
    orch._embed_index = _FauxEmbedIndex(hits=[], miss="moteur d'embeddings indisponible")

    out = orch._tool_find_relevant_files({"query": "routeur adaptatif"})

    assert "aucun fichier pertinent" not in out.lower()


def test_find_relevant_files_transmet_le_k_demande(orch):
    """`k` arrive du LLM en texte aussi souvent qu'en entier."""
    index = _FauxEmbedIndex(hits=[])
    orch._embed_index = index

    orch._tool_find_relevant_files({"query": "x", "k": "3"})

    assert index.dernier_k == 3


def test_find_relevant_files_k_par_defaut(orch):
    index = _FauxEmbedIndex(hits=[])
    orch._embed_index = index

    orch._tool_find_relevant_files({"query": "x"})

    assert index.dernier_k == 5


# --- coercition d'arguments venus du LLM ------------------------------------


@pytest.mark.parametrize("brut,attendu", [("50", 50), (None, 100), ("abc", 100), ("", 100)])
def test_run_sql_coerce_max_rows(orch, monkeypatch, brut, attendu):
    """Le LLM n'a aucune garantie de type : un `max_rows` illisible doit retomber
    sur le défaut, pas faire lever l'adaptateur."""
    vus = {}

    def faux_run_sql(query, database, *, mode, params, max_rows):
        vus["max_rows"] = max_rows
        return {"ok": True, "rows": [], "columns": []}

    monkeypatch.setattr("tools.sql_runner.run_sql", faux_run_sql)
    monkeypatch.setattr("tools.sql_runner.format_sql_result", lambda r: "ok")

    orch._tool_run_sql({"query": "SELECT 1", "database": "x.db", "max_rows": brut})

    assert vus["max_rows"] == attendu


def test_run_sql_defauts_de_mode_et_params(orch, monkeypatch):
    vus = {}

    def faux_run_sql(query, database, *, mode, params, max_rows):
        vus.update(mode=mode, params=params, query=query)
        return {}

    monkeypatch.setattr("tools.sql_runner.run_sql", faux_run_sql)
    monkeypatch.setattr("tools.sql_runner.format_sql_result", lambda r: "ok")

    orch._tool_run_sql({"query": "SELECT 1", "database": "x.db"})

    # Lecture seule par défaut : un mode absent ne doit jamais valoir « write ».
    assert vus["mode"] == "read"
    assert vus["params"] is None

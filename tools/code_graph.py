"""Lecture du graphe de connaissance code (graphify) — lecture seule, on-demand.

`graphify` (CLI externe, build AST-only HORS-LIGNE, zéro coût LLM) produit
`graphify-out/graph.json` : un graphe networkx « node-link » des symboles du
projet (fonctions, classes, méthodes) et de leurs relations (`calls`,
`contains`, `method`, `imports`…). Ce module se contente de LIRE ce fichier.

Choix d'archi : AUCUNE dépendance runtime sur le package `graphify` ni sur
`networkx` (parsing JSON pur + BFS maison). Klody gagne la capacité « graphe »
sans nouvelle dépendance lourde ; le seul prérequis est que `graph.json` existe,
construit par `graphify update .` (hook post-commit, ~3 s).

Pourquoi ce module EN PLUS de find_symbol / find_references :
- `find_symbol`     répond déjà « où est défini X » → on ne le double pas.
- `find_references` donne une liste plate `fichier:ligne` SANS nommer la fonction
  appelante, en 1 saut, sans notion de centralité.
Ce module apporte le DELTA que l'index actuel ne sait pas faire :
- `path A B`   : plus court chemin multi-sauts entre deux symboles.
- `overview`   : god nodes (centralité de degré) + nb de communautés.
- `explain X`  : voisins TYPÉS avec le NOM du nœud appelant / appelé.

Limite connue : extraction AST → le dispatch dynamique (table de lambdas de
l'orchestrateur, MCP, getattr) n'apparaît pas en arête `calls` directe ; ces
liens ressortent au mieux en arêtes `INFERRED` (confiance basse) ou pas du tout.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Chemin du graphe relatif à la racine projet (défaut graphify).
_GRAPH_REL = "graphify-out/graph.json"

# Garde-fous d'affichage (réinjection LLM → on plafonne tout).
_MAX_NEIGHBORS = 18
_MAX_GODNODES = 12
_MAX_AMBIGUOUS = 6
_MAX_PATH_HOPS = 8

# Marqueurs de code vendored / généré : un graphe construit à la racine
# (~/Projets) ne bénéficie pas du .gitignore par-repo → des libs bundlées
# (three.module.js…) deviennent les god nodes #1 et noient la carte. On les
# écarte du classement `overview` (les requêtes ciblées par symbole, elles,
# restent libres d'atteindre un nœud vendored si l'utilisateur le vise).
_VENDOR_MARKERS = (
    "node_modules/", "/vendor/", "_preview/", ".venv/", "site-packages/",
    "/dist/", "/build/", ".min.js", "/third_party/",
)


def _is_vendor(n: dict) -> bool:
    sf = (n.get("source_file") or "").lower()
    return any(m in sf for m in _VENDOR_MARKERS)


@dataclass
class _Graph:
    """Graphe chargé en mémoire, dérivé de graph.json (cache par mtime)."""
    mtime: float
    nodes: dict[str, dict] = field(default_factory=dict)          # id → node
    out_edges: dict[str, list[dict]] = field(default_factory=dict)  # id → [edge…]
    in_edges: dict[str, list[dict]] = field(default_factory=dict)
    built_at_commit: str = ""

    def degree(self, nid: str) -> int:
        return len(self.out_edges.get(nid, ())) + len(self.in_edges.get(nid, ()))


# Cache process-level par racine (l'Orchestrator est recréé à chaque tour ; on
# évite de relire/parser 3 Mo de JSON à chaque appel — invalidé sur mtime).
_CACHE: dict[str, _Graph] = {}


def _graph_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / _GRAPH_REL


def _load(project_root: Path | str) -> _Graph | None:
    """Charge (ou recharge si modifié) le graphe. None si absent/illisible."""
    gp = _graph_path(project_root)
    try:
        mtime = gp.stat().st_mtime
    except OSError:
        return None
    key = str(gp)
    cached = _CACHE.get(key)
    if cached and cached.mtime >= mtime:
        return cached
    try:
        raw = json.loads(gp.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        logger.warning("[code_graph] lecture %s échouée: %s", gp, exc)
        return None

    g = _Graph(mtime=mtime, built_at_commit=str(raw.get("built_at_commit", "")))
    for n in raw.get("nodes", ()):
        nid = n.get("id")
        if nid:
            g.nodes[nid] = n
    for e in raw.get("links", ()):
        s, t = e.get("source"), e.get("target")
        if s is None or t is None:
            continue
        g.out_edges.setdefault(s, []).append(e)
        g.in_edges.setdefault(t, []).append(e)
    _CACHE[key] = g
    return g


# ---------------------------------------------------------------------------- #
# Résolution de symbole (nom humain → node id)                                 #
# ---------------------------------------------------------------------------- #


def _clean(label: str) -> str:
    """Normalise un label pour matcher (`._execute_tool()` → `_execute_tool`)."""
    return label.strip().lstrip(".").rstrip("()").lower()


def _match(g: _Graph, query: str) -> tuple[list[str], bool]:
    """Retourne (ids candidats, exact?). Priorise le match exact de label."""
    q = _clean(query)
    if not q:
        return [], False
    exact, partial = [], []
    for nid, n in g.nodes.items():
        # On ignore les nœuds « rationale » (docstrings) : bruit pour la nav.
        if "_rationale_" in nid:
            continue
        lab = _clean(n.get("label", ""))
        if lab == q:
            exact.append(nid)
        elif q in lab or q in nid.lower():
            partial.append(nid)
    if exact:
        return exact, True
    return partial, False


def _loc(n: dict) -> str:
    sf = n.get("source_file", "?")
    sl = n.get("source_location", "")
    return f"{sf}:{sl}" if sl else sf


def _label(g: _Graph, nid: str) -> str:
    return g.nodes.get(nid, {}).get("label", nid)


# ---------------------------------------------------------------------------- #
# Opérations                                                                   #
# ---------------------------------------------------------------------------- #


def explain(project_root: Path | str, symbol: str) -> str:
    g = _load(project_root)
    if g is None:
        return _absent()
    ids, _ = _match(g, symbol)
    if not ids:
        return f"Aucun nœud `{symbol}` dans le graphe. (Essaie find_symbol.)"
    if len(ids) > 1:
        return _ambiguous(g, symbol, ids)
    nid = ids[0]
    n = g.nodes[nid]
    lines = [
        f"Nœud `{n.get('label', nid)}` — {_loc(n)}",
        f"  communauté {n.get('community', '?')} · degré {g.degree(nid)}",
    ]
    outs = g.out_edges.get(nid, [])
    ins = g.in_edges.get(nid, [])
    if ins:
        lines.append(f"Appelé / contenu par ({len(ins)}) :")
        for e in ins[:_MAX_NEIGHBORS]:
            lines.append(
                f"  <-- {_label(g, e['source'])} "
                f"[{e.get('relation', '?')}/{e.get('confidence', '?')}]")
        if len(ins) > _MAX_NEIGHBORS:
            lines.append(f"  … +{len(ins) - _MAX_NEIGHBORS}")
    if outs:
        lines.append(f"Appelle / contient ({len(outs)}) :")
        for e in outs[:_MAX_NEIGHBORS]:
            lines.append(
                f"  --> {_label(g, e['target'])} "
                f"[{e.get('relation', '?')}/{e.get('confidence', '?')}]")
        if len(outs) > _MAX_NEIGHBORS:
            lines.append(f"  … +{len(outs) - _MAX_NEIGHBORS}")
    return "\n".join(lines)


def callers(project_root: Path | str, symbol: str) -> str:
    """« Qui appelle X » — arêtes entrantes nommées (le + de find_references)."""
    g = _load(project_root)
    if g is None:
        return _absent()
    ids, _ = _match(g, symbol)
    if not ids:
        return f"Aucun nœud `{symbol}` dans le graphe."
    if len(ids) > 1:
        return _ambiguous(g, symbol, ids)
    nid = ids[0]
    ins = g.in_edges.get(nid, [])
    if not ins:
        return f"`{_label(g, nid)}` : aucun appelant connu dans le graphe."
    lines = [f"Appelants de `{_label(g, nid)}` ({len(ins)}) :"]
    for e in ins[:_MAX_NEIGHBORS]:
        src = g.nodes.get(e["source"], {})
        lines.append(
            f"  • {src.get('label', e['source'])} — {_loc(src)} "
            f"[{e.get('relation', '?')}/{e.get('confidence', '?')}]")
    if len(ins) > _MAX_NEIGHBORS:
        lines.append(f"  … +{len(ins) - _MAX_NEIGHBORS}")
    return "\n".join(lines)


def path(project_root: Path | str, a: str, b: str) -> str:
    """Plus court chemin non-orienté entre deux symboles (BFS). Le + d'index."""
    g = _load(project_root)
    if g is None:
        return _absent()
    ids_a, _ = _match(g, a)
    ids_b, _ = _match(g, b)
    if not ids_a:
        return f"Aucun nœud `{a}`."
    if not ids_b:
        return f"Aucun nœud `{b}`."
    src = ids_a[0]
    targets = set(ids_b)
    # BFS non-orienté (les arêtes calls/contains sont dirigées mais pour « comment
    # X et Y sont-ils reliés » on veut le chemin, pas le sens).
    prev: dict[str, tuple[str, dict] | None] = {src: None}
    q: deque[str] = deque([src])
    hit: str | None = src if src in targets else None
    while q and hit is None:
        cur = q.popleft()
        for e in g.out_edges.get(cur, []) + g.in_edges.get(cur, []):
            nxt = e["target"] if e["source"] == cur else e["source"]
            if nxt in prev:
                continue
            prev[nxt] = (cur, e)
            if nxt in targets:
                hit = nxt
                break
            q.append(nxt)
    if hit is None:
        return f"Pas de chemin entre `{a}` et `{b}` dans le graphe."
    # Reconstruit
    chain: list[tuple[str, dict | None]] = []
    cur2: str | None = hit
    while cur2 is not None:
        step = prev[cur2]
        chain.append((cur2, step[1] if step else None))
        cur2 = step[0] if step else None
    chain.reverse()
    hops = len(chain) - 1
    if hops > _MAX_PATH_HOPS:
        return (f"Chemin trouvé ({hops} sauts) mais trop long pour affichage "
                f"(>{_MAX_PATH_HOPS}). Reformule avec des symboles plus proches.")
    parts = [_label(g, chain[0][0])]
    for nid, edge in chain[1:]:
        rel = edge.get("relation", "?") if edge else "?"
        parts.append(f"--{rel}--> {_label(g, nid)}")
    return f"Chemin ({hops} sauts) : " + " ".join(parts)


def overview(project_root: Path | str) -> str:
    """Carte structurelle compacte : god nodes + communautés. Le + d'index."""
    g = _load(project_root)
    if g is None:
        return _absent()
    code_nodes = [nid for nid, n in g.nodes.items()
                  if "_rationale_" not in nid and not _is_vendor(n)]
    god = sorted(code_nodes, key=g.degree, reverse=True)[:_MAX_GODNODES]
    communities = {n.get("community") for n in g.nodes.values()
                   if n.get("community") is not None}
    edges = sum(len(v) for v in g.out_edges.values())
    lines = [
        f"Carte du code (commit {g.built_at_commit[:8] or '?'}) : "
        f"{len(code_nodes)} symboles · {edges} arêtes · "
        f"{len(communities)} communautés.",
        "God nodes (les + connectés — points d'entrée structurels) :",
    ]
    for nid in god:
        n = g.nodes[nid]
        lines.append(f"  • [deg {g.degree(nid):>3}] {n.get('label', nid)} — {_loc(n)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- #
# Helpers d'affichage                                                          #
# ---------------------------------------------------------------------------- #


def _absent() -> str:
    return ("Graphe code absent (graphify-out/graph.json introuvable). "
            "Construis-le hors-ligne : `graphify update .` (zéro coût LLM). "
            "En attendant, utilise find_symbol / find_references.")


def _ambiguous(g: _Graph, symbol: str, ids: list[str]) -> str:
    lines = [f"`{symbol}` ambigu — {len(ids)} candidats, précise :"]
    for nid in ids[:_MAX_AMBIGUOUS]:
        n = g.nodes[nid]
        lines.append(f"  • {n.get('label', nid)} — {_loc(n)}")
    if len(ids) > _MAX_AMBIGUOUS:
        lines.append(f"  … +{len(ids) - _MAX_AMBIGUOUS}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- #
# Dispatch (appelé par l'orchestrateur)                                        #
# ---------------------------------------------------------------------------- #


def query(project_root: Path | str, a: dict) -> str:
    """Routeur de l'outil `code_graph`. `mode` ∈ explain|callers|path|overview."""
    mode = (a.get("mode") or "explain").strip().lower()
    if mode == "overview":
        return overview(project_root)
    if mode == "path":
        if not a.get("symbol") or not a.get("to"):
            return "Mode `path` : fournis `symbol` ET `to`."
        return path(project_root, a["symbol"], a["to"])
    if not a.get("symbol"):
        return f"Mode `{mode}` : fournis `symbol`."
    if mode == "callers":
        return callers(project_root, a["symbol"])
    return explain(project_root, a["symbol"])

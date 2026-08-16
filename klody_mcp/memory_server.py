"""Serveur MCP mémoire — expose la mémoire sémantique de Klody à un client MCP.

Pourquoi ce serveur existe (analyse d'un post r/LocalLLaMA du 2026-08-16) : quand
on paie déjà un LLM cloud (Codex, ChatGPT, Claude), le meilleur usage du matériel
local n'est pas de courir après un LLM local, mais de servir ce que le cloud ne
donne PAS commodément — embeddings + mémoire. Klody avait déjà toute la
machinerie (`agent/semantic_memory.py` : bge-m3 en process, SQLite + sqlite-vec +
FTS5, fusion RRF), mais elle n'était offerte QU'EN process, via l'outil ReAct
`rappeler_memoire`. Aucun client externe (Codex, ChatGPT Web, Claude Desktop) ne
pouvait donc partager cette mémoire. Ce serveur ferme ce trou : la MÊME base,
la MÊME barrière de sanitisation, exposée en MCP. 100 % local — aucune nouvelle
dépendance, aucun modèle de plus.

Ce que ce serveur NE fait PAS, délibérément :
- Il n'ajoute AUCUN reranker cross-encoder (le « Qwen3-Reranker-4B » du post). La
  preuve mesurée du dépôt dit que la précision de retrieval n'est pas le goulot
  (cf. CLAUDE.md, encadré « l'ouverture de docs/ décide de tout ») et la règle
  d'or interdit toute amélioration non chiffrée au bench. Le retrieval reste
  l'hybride RRF existant.
- Il ne réimplémente rien : il DÉLÈGUE à `agent.semantic_memory`, seule source de
  vérité (une seconde copie de la logique divergerait en silence).

Règle de frontière : un outil MCP ne LÈVE JAMAIS. Toute indisponibilité (paquet
`klody-memory` absent, mémoire désactivée, erreur moteur) devient un dict lisible.
C'est la même philosophie que `recall_for_llm` : l'appelant est un LLM, pas un
`try/except`. La barrière de sanitisation ASI06 reste au rendu (`recall_for_llm`
sanitise chaque souvenir) — ce serveur ne rend jamais de brut.

Démarrage :
    python -m klody_mcp.memory_server                          # stdio (défaut)
    MEMORY_MCP_TRANSPORT=http python -m klody_mcp.memory_server # :8095

Outils exposés :
- memoriser(texte, titre, kind, remplacer)  — écrit un souvenir + l'embedde
- rappeler(requete, top_k, kind)            — rappel hybride, texte sanitisé
- oublier(titre, kind)                      — supprime les souvenirs d'un titre
- etat_memoire()                            — disponible ? actif ? pourquoi pas ?
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastmcp import FastMCP

import config
from agent import semantic_memory as sm

load_dotenv()

logger = logging.getLogger(__name__)

mcp = FastMCP("KlodyMemory")


# ---------------------------------------------------------------------------- #
# Logique pure (testable, ne lève jamais) — les outils MCP y délèguent.         #
# ---------------------------------------------------------------------------- #


def _kind_or_none(kind: str) -> str | None:
    """`""` (défaut MCP, un str est plus simple qu'un `str | None` côté schéma)
    devient None → « tous types »."""
    kind = (kind or "").strip()
    return kind or None


def _raison_indispo() -> str:
    """Nomme la VRAIE cause de l'indisponibilité (jamais « mémoire vide »
    indifférencié — même exigence que les sondes du dépôt)."""
    if not config.SEMANTIC_MEMORY_ENABLED:
        return "désactivée (SEMANTIC_MEMORY_ENABLED=0)"
    if not sm.MEMORY_AVAILABLE:
        return (f"paquet 'klody-memory' absent ({sm._IMPORT_ERROR!r}) — "
                "pip install -e ~/klody-core/memory sentence-transformers sqlite-vec")
    return ""


def memoriser_impl(texte: str, titre: str, kind: str = "context",
                   remplacer: bool = False) -> dict:
    """Écrit un souvenir. Retourne {ok, id, titre, kind} ou {ok: False, erreur}."""
    if not config.SEMANTIC_MEMORY_ENABLED:
        return {"ok": False, "erreur": "Mémoire désactivée (SEMANTIC_MEMORY_ENABLED=0)."}
    if not sm.MEMORY_AVAILABLE:
        return {"ok": False, "erreur": f"Mémoire indisponible : {_raison_indispo()}"}
    kind = (kind or "context").strip() or "context"
    try:
        book_id = sm.remember(texte, title=titre, kind=kind, replace=bool(remplacer))
    except ValueError as e:  # texte/titre vide — erreur d'appel, message direct
        return {"ok": False, "erreur": str(e)}
    except Exception as e:  # noqa: BLE001 — frontière MCP : jamais de propagation
        logger.warning("[memory_mcp] memoriser en échec : %s", e)
        return {"ok": False, "erreur": f"{e.__class__.__name__}: {e}"}
    return {"ok": True, "id": book_id, "titre": titre.strip(), "kind": kind,
            "remplace": bool(remplacer)}


def rappeler_impl(requete: str, top_k: int = 5, kind: str = "") -> dict:
    """Rappel hybride (FTS5 + vectoriel, RRF). `souvenirs` est le texte DÉJÀ
    sanitisé par recall_for_llm (barrière ASI06 conservée)."""
    requete = (requete or "").strip()
    if not requete:
        return {"ok": False, "erreur": "rappeler(): requête vide."}
    # recall_for_llm ne lève jamais et gère lui-même désactivation/indisponibilité.
    souvenirs = sm.recall_for_llm(requete, top_k=top_k, kind=_kind_or_none(kind))
    return {"ok": True, "requete": requete, "kind": _kind_or_none(kind) or "tous",
            "souvenirs": souvenirs}


def oublier_impl(titre: str, kind: str = "") -> dict:
    """Supprime les souvenirs d'un titre. Retourne {ok, supprimes} ou {ok: False}."""
    if not config.SEMANTIC_MEMORY_ENABLED:
        return {"ok": False, "erreur": "Mémoire désactivée (SEMANTIC_MEMORY_ENABLED=0)."}
    if not sm.MEMORY_AVAILABLE:
        return {"ok": False, "erreur": f"Mémoire indisponible : {_raison_indispo()}"}
    titre = (titre or "").strip()
    if not titre:
        return {"ok": False, "erreur": "oublier(): titre requis."}
    try:
        n = sm.forget(titre, kind=_kind_or_none(kind))
    except Exception as e:  # noqa: BLE001 — frontière MCP
        logger.warning("[memory_mcp] oublier en échec : %s", e)
        return {"ok": False, "erreur": f"{e.__class__.__name__}: {e}"}
    return {"ok": True, "titre": titre, "kind": _kind_or_none(kind) or "tous",
            "supprimes": n}


def etat_memoire_impl() -> dict:
    """Trois verdicts, jamais deux : disponible / désactivée / indisponible, et la
    RAISON quand ce n'est pas disponible."""
    disponible = bool(config.SEMANTIC_MEMORY_ENABLED and sm.MEMORY_AVAILABLE)
    return {
        "disponible": disponible,
        "active": bool(config.SEMANTIC_MEMORY_ENABLED),
        "moteur_installe": bool(sm.MEMORY_AVAILABLE),
        "provider": config.SEMANTIC_MEMORY_PROVIDER,
        "base": str(config.SEMANTIC_MEMORY_DB),
        "raison_indispo": "" if disponible else _raison_indispo(),
    }


# ---------------------------------------------------------------------------- #
# Outils MCP — fines enveloppes sur la logique pure ci-dessus.                  #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def memoriser(texte: str, titre: str, kind: str = "context",
                    remplacer: bool = False) -> dict:
    """Mémorise un fait durable et l'embedde (bge-m3, 100 % local).

    Args:
        texte: le contenu à retenir (fait concis, décision, préférence…).
        titre: clé du souvenir — sert de clé de mise à jour si remplacer=True.
        kind: catégorie filtrable au rappel (ex. "context", "projet", "profil").
        remplacer: True supprime d'abord les souvenirs de même (titre, kind)
            avant d'écrire — évite de dupliquer un souvenir mis à jour.
    """
    return memoriser_impl(texte, titre, kind=kind, remplacer=remplacer)


@mcp.tool()
async def rappeler(requete: str, top_k: int = 5, kind: str = "") -> dict:
    """Rappelle les souvenirs les plus proches d'une requête (rappel hybride
    FTS5 + vectoriel fusionné par RRF).

    Args:
        requete: ce qu'on cherche à retrouver.
        top_k: nombre de souvenirs (borné 1..20 côté moteur).
        kind: restreint à une catégorie (vide = toutes).
    """
    return rappeler_impl(requete, top_k=top_k, kind=kind)


@mcp.tool()
async def oublier(titre: str, kind: str = "") -> dict:
    """Oublie tous les souvenirs portant ce titre.

    Args:
        titre: clé des souvenirs à supprimer.
        kind: restreint la suppression à une catégorie (vide = toutes).
    """
    return oublier_impl(titre, kind=kind)


@mcp.tool()
async def etat_memoire() -> dict:
    """Dit si la mémoire est disponible, active, et sinon POURQUOI (moteur absent
    ou mémoire désactivée) — jamais un « rien » indifférencié."""
    return etat_memoire_impl()


# ---------------------------------------------------------------------------- #
# Entrée principale                                                            #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("MEMORY_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("MEMORY_MCP_PORT", "8095"))
    host = os.getenv("MEMORY_MCP_HOST", "127.0.0.1")

    if transport == "http":
        logger.info("Memory MCP HTTP : http://%s:%d (base %s)", host, port,
                    config.SEMANTIC_MEMORY_DB)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("Memory MCP stdio (base %s)", config.SEMANTIC_MEMORY_DB)
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

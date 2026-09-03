#!/usr/bin/env python3
"""Mesure le surcoût fixe par tour (tokens injectés avant le premier message).

Assemble le prompt système exactement comme Orchestrator._inject_system_prompt
le fait et rend le décompte par poste. « Fixe » = indépendant du message
utilisateur courant (query="").

Sortie lisible sur stderr, JSON sur stdout avec --json. Deux JSON comparés
avec --comparer a.json b.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from agent.prompts import compose_system_prompt
from agent.tokens import count_tokens, tokenizer_is_exact
from tools.registry import get_tools
from tools.skills import format_skills_for_prompt, load_skills, select_skills


def _poste_absent(raison: str) -> dict:
    return {"absent": True, "raison": raison}


def mesurer(task_type: str | None = None) -> dict:
    """Mesure chaque poste du prompt système. Fidèle à _inject_system_prompt."""
    postes: dict[str, dict] = {}

    # -- 1. Prompt de base + type + ligne PROJECT_ROOT ----------------------
    base_prompt = compose_system_prompt(task_type)
    texte_base = f"{base_prompt}\n\nDossier projet actif: {config.PROJECT_ROOT}"
    postes["prompt_base_type"] = {
        "tokens": count_tokens(texte_base),
        "type_utilise": task_type or "default",
    }

    # -- 2. Skills always-on (query="" → seuls les always-on passent) -------
    try:
        skills = select_skills(load_skills(), "")
        section = format_skills_for_prompt(skills) if skills else ""
        postes["skills"] = {
            "tokens": count_tokens(section),
            "nombre": len(skills),
        }
    except Exception as exc:
        postes["skills"] = _poste_absent(str(exc))

    # -- 3. Mémoire long terme ---------------------------------------------
    try:
        from agent.long_term_memory import get_long_term_memory
        lt = get_long_term_memory()
        section = lt.format_for_prompt()
        postes["memoire_lt"] = {
            "tokens": count_tokens(section),
            "entrees": len(lt.entries),
        }
    except Exception as exc:
        postes["memoire_lt"] = _poste_absent(str(exc))

    # -- 4. Retrieval proactif (vide avec query="", coût variable) ----------
    postes["retrieval"] = {
        "tokens": 0,
        "note": "query vide → section vide (coût variable, pas fixe)",
    }

    # -- 5. Profil utilisateur ----------------------------------------------
    try:
        from agent.profiler import get_profiler
        profiler = get_profiler()
        section = profiler.get_profile_for_prompt()
        postes["profil"] = {
            "tokens": count_tokens(section),
            "requetes_vues": profiler.total_requests,
        }
    except Exception as exc:
        postes["profil"] = _poste_absent(str(exc))

    # -- 6. Conventions auto-détectées --------------------------------------
    try:
        from agent.conventions import ConventionDetector
        conv = ConventionDetector(config.PROJECT_ROOT)
        section = conv.detect().format_for_prompt()
        postes["conventions"] = {
            "tokens": count_tokens(section),
        }
    except Exception as exc:
        postes["conventions"] = _poste_absent(str(exc))

    # -- 7. Erreurs récurrentes ---------------------------------------------
    try:
        from agent.error_memory import ErrorMemory
        em = ErrorMemory(workdir=config.PROJECT_ROOT)
        section = em.format_for_prompt()
        postes["erreurs"] = {
            "tokens": count_tokens(section),
            "entrees": len(em.entries),
        }
    except Exception as exc:
        postes["erreurs"] = _poste_absent(str(exc))

    # -- 8. Schémas d'outils (registry + MCP) -------------------------------
    tools = get_tools()
    n_mcp = 0
    try:
        if config.MCP_SERVERS:
            from tools.mcp_bridge import MCPManager
            mgr = MCPManager(config.MCP_SERVERS)
            mcp_tools = mgr.discover()
            tools = [*tools, *mcp_tools]
            n_mcp = len(mcp_tools)
    except Exception:  # MCP optionnel — serveurs éteints ou non configurés
        pass
    tool_json = json.dumps(tools, ensure_ascii=False)
    postes["schemas_outils"] = {
        "tokens": count_tokens(tool_json),
        "nombre_registry": len(tools) - n_mcp,
        "nombre_mcp": n_mcp,
    }

    total = sum(p["tokens"] for p in postes.values() if "tokens" in p)

    return {
        "postes": postes,
        "total_tokens": total,
        "tokenizer_exact": tokenizer_is_exact(),
        "horodatage": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def afficher(resultat: dict) -> None:
    """Tableau lisible sur stderr."""
    print("", file=sys.stderr)
    print(f"{'Poste':<30} {'Tokens':>8}  Détail", file=sys.stderr)
    print("─" * 70, file=sys.stderr)
    for nom, val in resultat["postes"].items():
        if val.get("absent"):
            print(f"{nom:<30} {'absent':>8}  {val['raison']}", file=sys.stderr)
        else:
            detail_parts = []
            for k, v in val.items():
                if k not in ("tokens", "absent", "raison"):
                    detail_parts.append(f"{k}={v}")
            detail = ", ".join(detail_parts) if detail_parts else ""
            print(f"{nom:<30} {val['tokens']:>8}  {detail}", file=sys.stderr)
    print("─" * 70, file=sys.stderr)
    total = resultat["total_tokens"]
    exact = "exact" if resultat["tokenizer_exact"] else "heuristique ~chars/4"
    print(f"{'TOTAL':<30} {total:>8}  ({exact})", file=sys.stderr)
    print("", file=sys.stderr)


def comparer(path_a: str, path_b: str) -> None:
    """Compare deux JSON de mesure et affiche les deltas."""
    a = json.loads(Path(path_a).read_text(encoding="utf-8"))
    b = json.loads(Path(path_b).read_text(encoding="utf-8"))

    print(f"\nComparaison : {Path(path_a).name}  →  {Path(path_b).name}", file=sys.stderr)
    print(f"{'Poste':<30} {'Avant':>8} {'Après':>8} {'Δ':>8}", file=sys.stderr)
    print("─" * 70, file=sys.stderr)

    tous_postes = dict.fromkeys([*a["postes"], *b["postes"]])
    for nom in tous_postes:
        pa = a["postes"].get(nom, {})
        pb = b["postes"].get(nom, {})
        ta = pa.get("tokens", "—") if not pa.get("absent") else "absent"
        tb = pb.get("tokens", "—") if not pb.get("absent") else "absent"
        if isinstance(ta, int) and isinstance(tb, int):
            delta = tb - ta
            signe = "+" if delta > 0 else ""
            print(f"{nom:<30} {ta:>8} {tb:>8} {signe}{delta:>7}", file=sys.stderr)
        else:
            print(f"{nom:<30} {ta!s:>8} {tb!s:>8} {'—':>8}", file=sys.stderr)

    ta_total = a["total_tokens"]
    tb_total = b["total_tokens"]
    delta = tb_total - ta_total
    signe = "+" if delta > 0 else ""
    print("─" * 70, file=sys.stderr)
    print(f"{'TOTAL':<30} {ta_total:>8} {tb_total:>8} {signe}{delta:>7}", file=sys.stderr)
    print("", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", dest="task_type", default=None,
                        help="task_type du routeur (feature, bug_fix…). Défaut : default.")
    parser.add_argument("--json", action="store_true",
                        help="Sortie JSON sur stdout (tableau toujours sur stderr).")
    parser.add_argument("--comparer", nargs=2, metavar=("AVANT", "APRES"),
                        help="Compare deux JSON de mesure au lieu de mesurer.")
    args = parser.parse_args()

    if args.comparer:
        comparer(*args.comparer)
        return

    resultat = mesurer(args.task_type)
    afficher(resultat)

    if args.json:
        json.dump(resultat, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()

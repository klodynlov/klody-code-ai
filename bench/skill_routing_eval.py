"""Mini-éval de routage de skills : IDF (`select_skills`) vs sémantique (`SkillRouter`).

But : décider OBJECTIVEMENT s'il vaut la peine d'activer le routeur sémantique
(`SKILLS_ROUTER_ENABLED`). On mesure, sur un jeu de requêtes étiquetées :
  - hit@1 / hit@3 : le skill attendu est-il en tête / dans le top-k ?
  - accord IDF↔sémantique : routent-ils pareil en rang 1 ?

Le routeur sémantique a besoin d'Ollama (`/api/embed`, bge-m3) + du LLM (:8080).
S'ils sont indisponibles, `SkillRouter.select()` dégrade vers `select_skills` :
l'éval sémantique devient alors identique à l'IDF — c'est signalé en tête.

Usage :
    .venv/bin/python -m bench.skill_routing_eval            # IDF + sémantique
    .venv/bin/python -m bench.skill_routing_eval --no-judge # embeddings sans juge LLM
    .venv/bin/python -m bench.skill_routing_eval --k 3
"""
from __future__ import annotations

import argparse

from tools.skill_router import SkillRouter
from tools.skills import load_skills, select_skills

# Requêtes étiquetées : (requête, slug attendu). Mélange de cas « mots-clés
# évidents » (l'IDF devrait gagner) et de paraphrases sémantiques (là où un
# routeur à embeddings peut faire mieux que le recouvrement lexical).
DATASET: list[tuple[str, str]] = [
    ("comment brancher un nouveau serveur MCP à klody", "ajouter_un_serveur_mcp_a_klody"),
    ("mon serveur local s'arrête quand je ferme ma session, fixe ça",
     "rendre_un_service_local_permanent_launch"),
    ("rends mon daemon persistant après un redémarrage", "rendre_un_service_local_permanent_launch"),
    ("ajoute un mode sombre à l'interface", "theme_clair_sombre_auto_ui_tauri_2_react"),
    ("lire une URL externe sans risque de SSRF", "garde-fou_anti-ssrf_pour_un_fetch_web"),
    ("récupère une page web côté agent sans exposer le réseau interne",
     "garde-fou_anti-ssrf_pour_un_fetch_web"),
    ("explique l'algorithme de Dijkstra et sa complexité", "maitriser_les_algorithmes"),
    ("quelle structure de données pour une file de priorité", "maitriser_les_algorithmes"),
    ("distille ces 5 livres en une seule méthode actionnable", "distiller_plusieurs_livres"),
    ("résume ce livre en une checklist", "distiller_un_livre"),
    ("croise ces articles et dis-moi ce qui fait consensus",
     "croiser_des_sources_en_bonnes_pratiques"),
    ("transforme cette synthèse en skill permanent", "cristalliser_en_skill"),
    ("améliore mon prompt pour réduire les hallucinations", "promptoptimiser"),
    ("structurer un prompt avec des exemples (in-context learning)", "promptoptimiser"),
    ("aide-moi à rédiger le plan de ma thèse", "techniques_redaction_these"),
    ("quels réglages d'égaliseur dans Pro-Q de FabFilter", "outils_edition_audio_fabfilter"),
    ("comment créer et gérer des skills dans un agent IA",
     "connaissances_creation_et_gestion_de_ski"),
    ("quelles méthodes de programmation avancées appliquer",
     "methodes_de_programmation_avancees"),
]


def _howto_slugs(picks: list[dict]) -> list[str]:
    """Slugs des skills how-to retournés (on retire les permanents toujours injectés)."""
    return [s["slug"] for s in picks
            if not s["slug"].startswith(("utilisateur_", "conventions_"))]


def _hits(expected: str, picks: list[str]) -> tuple[bool, bool]:
    """(hit@1, hit@k) pour un skill attendu vs la liste ordonnée de slugs."""
    return (bool(picks) and picks[0] == expected, expected in picks)


def _mark(hit: bool) -> str:
    return "✓" if hit else "·"


def run(k: int = 3, use_judge: bool = True) -> dict:
    skills = load_skills()
    router = SkillRouter(use_llm_judge=use_judge)

    # Sonde : les embeddings répondent-ils vraiment ? (sinon sémantique == IDF)
    embeds_up = bool(router._embed_one("test de disponibilité"))

    rows: list[dict] = []
    idf_h1 = idf_hk = sem_h1 = sem_hk = agree = 0
    for query, expected in DATASET:
        idf = _howto_slugs(select_skills(skills, query, k=k))
        sem = _howto_slugs(router.select(query, k=k))
        i1, ik = _hits(expected, idf)
        s1, sk = _hits(expected, sem)
        idf_h1 += i1
        idf_hk += ik
        sem_h1 += s1
        sem_hk += sk
        same_top1 = bool(idf) and bool(sem) and idf[0] == sem[0]
        agree += same_top1
        rows.append({"query": query, "expected": expected,
                     "idf": idf, "sem": sem, "idf_hit1": i1, "sem_hit1": s1,
                     "agree": same_top1})

    n = len(DATASET)
    return {
        "n": n, "k": k, "use_judge": use_judge, "embeds_up": embeds_up,
        "idf_hit1": idf_h1, "idf_hitk": idf_hk,
        "sem_hit1": sem_h1, "sem_hitk": sem_hk,
        "agreement_top1": agree, "rows": rows,
    }


def _print_report(res: dict) -> None:
    n = res["n"]
    print("=" * 78)
    print(f"Éval routage skills — {n} requêtes, k={res['k']}, juge LLM={res['use_judge']}")
    if not res["embeds_up"]:
        print("⚠  Embeddings INDISPONIBLES → le routeur sémantique a dégradé vers l'IDF.")
        print("   Les colonnes IDF et SEM sont donc identiques (lance Ollama bge-m3 pour un vrai test).")
    print("=" * 78)
    for r in res["rows"]:
        print(f"[{_mark(r['idf_hit1'])} idf {_mark(r['sem_hit1'])} sem] {r['query'][:52]:52}")
        print(f"        attendu={r['expected']}")
        print(f"        idf→ {r['idf']}")
        print(f"        sem→ {r['sem']}")
    print("-" * 78)
    print(f"IDF       hit@1 = {res['idf_hit1']:2}/{n}   hit@{res['k']} = {res['idf_hitk']:2}/{n}")
    print(f"Sémantique hit@1 = {res['sem_hit1']:2}/{n}   hit@{res['k']} = {res['sem_hitk']:2}/{n}")
    print(f"Accord top-1 IDF↔sémantique = {res['agreement_top1']:2}/{n}")
    print("=" * 78)
    if res["sem_hit1"] > res["idf_hit1"]:
        verdict = (
            "Sémantique > IDF sur CE jeu. Activer SKILLS_ROUTER_ENABLED est à "
            "considérer — mais valider sur un set plus large/non biaisé et peser "
            "le coût (latence embeddings+juge, dépendance Ollama/LLM up)."
        )
    else:
        verdict = "IDF ≥ sémantique : garder l'IDF (offline, zéro dépendance) ; ne pas activer."
    print("VERDICT :", verdict)


def main() -> None:
    ap = argparse.ArgumentParser(description="Éval routage de skills : IDF vs sémantique.")
    ap.add_argument("--k", type=int, default=3, help="Top-k skills injectés (défaut 3).")
    ap.add_argument("--no-judge", action="store_true", help="Embeddings seuls, sans juge LLM.")
    args = ap.parse_args()
    _print_report(run(k=args.k, use_judge=not args.no_judge))


if __name__ == "__main__":
    main()

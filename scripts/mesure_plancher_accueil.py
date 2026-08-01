#!/usr/bin/env python3
"""Plancher de latence d'un accueil généré au démarrage — INSTRUMENT, pas porte.

Question tranchée par ce script : un « bonjour » produit par le modèle au
lancement de Klody coûte-t-il assez peu pour être SYNCHRONE (l'utilisateur
attend son prompt), ou faut-il le poser en tâche de fond avec repli muet ?

Trois bras, mesurés dans le MÊME run — c'est la seule comparaison qui vaille
(cf. CLAUDE.md : la même tâche du banc a rendu 34 s et 119 s d'un run à l'autre
sans qu'aucune variable ne change ; toute lecture de vitesse est intra-run) :

    plancher      max_tokens=1, aucun outil    → prefill + 1 token, le sol absolu
    accueil       micro-prompt, ~60 tokens     → le coût RÉEL de la fonctionnalité
    avec_outils   idem + les 69 schémas        → ce que coûterait le même accueil
                                                 s'il passait par orchestrator.run()

Le troisième bras existe pour VÉRIFIER une affirmation plutôt que la répéter :
les schémas d'outils pèsent ~12,5 k tokens de prefill, d'où « ne pas router
l'accueil par la boucle ReAct ». Tant que ce n'est pas mesuré, c'est une
estimation — et une estimation de perf s'est déjà trompée d'un facteur 57 dans
ce dépôt (dédoublonnage quadratique supposé ~5 s, mesuré 0,087 s).

Usage :
    python scripts/mesure_plancher_accueil.py                  # 3 passes, 3 bras
    python scripts/mesure_plancher_accueil.py --passes 5
    python scripts/mesure_plancher_accueil.py --json mesure.json

⚠️ La PREMIÈRE passe porte le chargement éventuel du modèle (44 Go pour brain,
voisin mesuré : coder, 30 Go, chargé en 8,3 s). Elle est affichée à part et
JAMAIS moyennée avec les autres : c'est précisément l'écart froid/chaud qui
décide entre accueil synchrone et accueil asynchrone.

Codes de sortie — « je n'ai pas pu mesurer » n'est PAS « j'ai mesuré, c'est
bon » :
    0 = mesure effectuée
    1 = mesure impossible (backend injoignable, alias inconnu, RAM refusée)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from tools.registry import TOOLS

# Micro-prompt représentatif : ce qu'un accueil généré enverrait vraiment. Court
# volontairement — l'intérêt de la fonctionnalité est de NE PAS charger base.md
# (595 tokens) ni les schémas d'outils.
_SYSTEME_ACCUEIL = (
    "Tu es Klody, agent de code local. Salue l'utilisateur en UNE phrase, "
    "en français, sans emoji et sans poser de question."
)
_UTILISATEUR_ACCUEIL = (
    "Session qui démarre. Projet : klody-code-ai. Dernière session : hier. "
    "Dis bonjour."
)


class MesureImpossible(RuntimeError):
    """Le backend n'a pas répondu de quoi mesurer. Distinct d'une mesure lente."""


def _diagnostic(status: int, corps: str) -> str:
    """Traduit un HTTP d'échec en cause ACTIONNABLE.

    Un 404 et un 503 disent des choses opposées, et le préflight du nightly les
    a confondus pendant une journée entière (`curl -sf` rend le même code pour
    les deux, `-s … > /dev/null` jette le message qui les sépare) :
      404 « modèle inconnu » → l'alias ne résout pas, c'est la panne du 2026-07-03.
      503 « RAM insuffisante » → l'alias résout parfaitement, le gateway connaît
          même son empreinte ; il manque de la place. Réessayer suffit souvent :
          `vm_stat` sous-estime la RAM disponible juste après un gros chargement.
    """
    extrait = (corps or "").strip()[:300]
    if status == 404:
        return (
            f"HTTP 404 — l'alias '{config.LLM_MODEL}' ne résout pas côté gateway. "
            f"Vérifier le registre du resolver. Réponse : {extrait}"
        )
    if status == 503:
        return (
            "HTTP 503 — le gateway a reconnu le modèle mais refuse de le charger "
            "(RAM). Ce n'est PAS un défaut de configuration : réessayer dans "
            f"1-2 min suffit généralement. Réponse : {extrait}"
        )
    return f"HTTP {status} — {extrait}"


def _appel(
    client: httpx.Client,
    *,
    messages: list[dict],
    max_tokens: int,
    outils: list[dict] | None,
) -> tuple[float, str | None, dict]:
    """Un aller-retour chronométré. Retourne (secondes, modèle servi, usage)."""
    charge: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if outils:
        charge["tools"] = outils

    t0 = time.monotonic()
    reponse = client.post("/chat/completions", json=charge)
    ecoule = time.monotonic() - t0

    if reponse.status_code != 200:
        raise MesureImpossible(_diagnostic(reponse.status_code, reponse.text))

    corps = reponse.json()
    return ecoule, corps.get("model"), corps.get("usage") or {}


_BRAS: dict[str, dict[str, Any]] = {
    "plancher": {
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "outils": None,
    },
    "accueil": {
        "messages": [
            {"role": "system", "content": _SYSTEME_ACCUEIL},
            {"role": "user", "content": _UTILISATEUR_ACCUEIL},
        ],
        "max_tokens": 60,
        "outils": None,
    },
    "avec_outils": {
        "messages": [
            {"role": "system", "content": _SYSTEME_ACCUEIL},
            {"role": "user", "content": _UTILISATEUR_ACCUEIL},
        ],
        "max_tokens": 60,
        "outils": TOOLS,
    },
}


def mesurer(passes: int) -> dict[str, Any]:
    entetes = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        entetes["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    releve: dict[str, list[float]] = {nom: [] for nom in _BRAS}
    usages: dict[str, dict] = {}
    servi: str | None = None

    with httpx.Client(
        base_url=config.LLM_BASE_URL.rstrip("/"),
        headers=entetes,
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
    ) as client:
        for passe in range(1, passes + 1):
            for nom, bras in _BRAS.items():
                try:
                    ecoule, modele, usage = _appel(
                        client,
                        messages=bras["messages"],
                        max_tokens=bras["max_tokens"],
                        outils=bras["outils"],
                    )
                except httpx.HTTPError as exc:
                    raise MesureImpossible(
                        f"{config.LLM_BASE_URL} injoignable ({type(exc).__name__}). "
                        f"En BACKEND={config.BACKEND}, la cible est le backend LLM — "
                        "PAS Ollama, qui ne sert que les embeddings (et plus depuis "
                        "SEMANTIC_MEMORY_PROVIDER=st)."
                    ) from exc
                releve[nom].append(round(ecoule, 3))
                usages[nom] = usage
                servi = modele or servi
                marque = "  (à froid)" if passe == 1 else ""
                print(f"  passe {passe}  {nom:<12} {ecoule:6.2f}s{marque}", flush=True)
            print(flush=True)

    return {
        "backend": config.BACKEND,
        "base_url": config.LLM_BASE_URL,
        "model_configured": config.LLM_MODEL,
        "model_served": servi,
        "passes": passes,
        "latences_s": releve,
        "usage_dernier": usages,
        "nb_outils": len(TOOLS),
    }


def _resume(releve: dict[str, list[float]]) -> None:
    print("┌─ Résumé (secondes) ────────────────────────────────────────────")
    print(f"│ {'bras':<12} {'1ʳᵉ passe':>10} {'médiane 2..N':>14} {'min':>8} {'max':>8}")
    for nom, valeurs in releve.items():
        if not valeurs:
            continue
        chaud = valeurs[1:] or valeurs
        print(
            f"│ {nom:<12} {valeurs[0]:>10.2f} {statistics.median(chaud):>14.2f} "
            f"{min(valeurs):>8.2f} {max(valeurs):>8.2f}"
        )
    print("└────────────────────────────────────────────────────────────────")
    print()
    print("⚠️  Ces chiffres ne valent QUE les uns contre les autres, dans ce run.")
    print("    Les comparer à un relevé d'un autre jour ne dit rien : mesuré le")
    print("    2026-07-30, la même tâche a rendu 34 s puis 119 s sans qu'aucune")
    print("    variable ne change.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--passes", type=int, default=3, help="Nombre de passes (défaut 3)")
    parser.add_argument("--json", type=str, default=None, metavar="FICHIER")
    args = parser.parse_args()

    print(f"→ backend {config.BACKEND} · {config.LLM_BASE_URL} · modèle demandé "
          f"« {config.LLM_MODEL} » · {len(TOOLS)} outils\n")

    try:
        mesure = mesurer(args.passes)
    except MesureImpossible as exc:
        print(f"✗ MESURE IMPOSSIBLE — {exc}", file=sys.stderr)
        print("  (aucun chiffre produit : ce n'est pas un résultat lent, c'est "
              "une absence de résultat)", file=sys.stderr)
        return 1

    _resume(mesure["latences_s"])
    print(f"\n  modèle réellement servi : {mesure['model_served']}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(mesure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  → {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

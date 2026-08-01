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
    python scripts/mesure_plancher_accueil.py --balayage       # coût des schémas
    python scripts/mesure_plancher_accueil.py --json mesure.json

⚠️ Le TOUT PREMIER appel du run porte le réveil du gateway — pas toute la
première passe. Mesuré le 2026-08-01 : `plancher` 6,52 s en tête de run, puis
`accueil` 0,46 s dans la même passe, modèle déjà chaud. Il est affiché à part et
jamais moyenné : c'est cet écart froid/chaud (0,13 s contre 6,52 s pour le même
appel) qui décide entre accueil synchrone et accueil asynchrone — la VARIANCE,
pas le coût nominal.

Le mode `--balayage` répond à une question que les trois bras ne tranchent pas :
les schémas d'outils coûtent-ils vraiment leur prefill ? Voir `balayer()`.

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
                # SEUL le tout premier appel est froid — pas toute la passe 1.
                # Mesuré le 2026-08-01 : plancher 6,52 s en tête de run, puis
                # accueil 0,46 s dans la même passe. Les 6,5 s étaient le réveil
                # du gateway, attribuées au bras qui passait en premier ; étiqueter
                # « à froid » les deux bras suivants faisait lire un coût de bras
                # là où il n'y en a pas.
                premier = passe == 1 and nom == next(iter(_BRAS))
                marque = "  (1ᵉʳ appel du run — porte le réveil du gateway)" if premier else ""
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


def balayer(passes: int) -> dict[str, Any]:
    """Isole le coût de prefill des schémas d'outils en faisant varier leur nombre.

    Pourquoi ce mode existe : dans le run du 2026-08-01, `avec_outils` a rendu
    4,06 s au premier appel puis 0,37 s ensuite, alors que le modèle était DÉJÀ
    chaud (`accueil` venait de rendre 0,46 s juste avant). L'explication plausible
    — les ~12,5 k tokens de schémas se paient une fois en prefill, puis le cache
    de prompt les rend gratuits — était invérifiable : n=1, et trois bras qui se
    suivent confondent le réveil du gateway avec le coût des schémas.

    Ici chaque appel envoie un SOUS-ENSEMBLE de taille différente. Deux tailles
    différentes = deux préfixes différents = cache de prompt froid à chaque fois,
    sans avoir à parier sur la façon dont le gateway sérialise les outils (un
    nonce placé dans le message système peut très bien atterrir APRÈS le bloc
    d'outils, qui resterait alors caché — d'où le choix de faire varier les
    outils eux-mêmes plutôt que le texte).

    LECTURE — et elle peut invalider une conclusion, c'est le but :
      latence qui CROÎT avec le nombre de schémas ⇒ le prefill se paie vraiment,
        et « ne pas router l'accueil par orchestrator.run() » tient debout ;
      colonne PLATE ⇒ les 4,06 s venaient d'autre chose, et cet argument tombe.
    """
    from agent.tokens import count_tokens, tokenizer_is_exact

    entetes = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        entetes["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    total = len(TOOLS)
    paliers = sorted({0, total // 4, total // 2, total})
    releve: list[dict[str, Any]] = []
    servi: str | None = None

    with httpx.Client(
        base_url=config.LLM_BASE_URL.rstrip("/"),
        headers=entetes,
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
    ) as client:
        for passe in range(1, passes + 1):
            for palier in paliers:
                # Décalage par passe : sans lui, la passe 2 rejouerait des préfixes
                # déjà vus et mesurerait le CACHE au lieu du prefill. On décale vers
                # le bas au dernier palier, faute de place au-dessus.
                if palier == 0:
                    n = 0
                elif palier + passe - 1 <= total:
                    n = palier + passe - 1
                else:
                    n = max(1, palier - (passe - 1))
                sous_ensemble = TOOLS[:n]
                try:
                    ecoule, modele, _usage = _appel(
                        client,
                        messages=_BRAS["accueil"]["messages"],
                        max_tokens=_BRAS["accueil"]["max_tokens"],
                        outils=sous_ensemble or None,
                    )
                except httpx.HTTPError as exc:
                    raise MesureImpossible(
                        f"{config.LLM_BASE_URL} injoignable ({type(exc).__name__})."
                    ) from exc
                jetons = count_tokens(json.dumps(sous_ensemble, ensure_ascii=False)) if n else 0
                servi = modele or servi
                releve.append({"passe": passe, "palier": palier, "outils": n,
                               "tokens": jetons, "latence_s": round(ecoule, 3)})
                print(f"  passe {passe}  {n:>3} outils  {jetons:>6} tokens  "
                      f"{ecoule:6.2f}s", flush=True)
            print(flush=True)

    return {
        "mode": "balayage",
        "backend": config.BACKEND,
        "base_url": config.LLM_BASE_URL,
        "model_configured": config.LLM_MODEL,
        "model_served": servi,
        "passes": passes,
        "paliers": paliers,
        "mesures": releve,
        "tokenizer_exact": tokenizer_is_exact(),
    }


def _resume_balayage(mesure: dict[str, Any]) -> None:
    # Groupé par PALIER, pas par nombre exact d'outils : le décalage anti-cache
    # fait varier ce nombre de ±(passes-1), et grouper dessus éclaterait chaque
    # palier en lignes à n=1 — une médiane sur un seul point n'en est pas une.
    par_palier: dict[int, list[float]] = {}
    jetons: dict[int, list[int]] = {}
    for ligne in mesure["mesures"]:
        par_palier.setdefault(ligne["palier"], []).append(ligne["latence_s"])
        jetons.setdefault(ligne["palier"], []).append(ligne["tokens"])

    print("┌─ Balayage : latence vs nombre de schémas d'outils ──────────────")
    print(f"│ {'palier':>7} {'tokens':>8} {'médiane':>9} {'min':>7} {'max':>7}")
    for n in sorted(par_palier):
        valeurs = par_palier[n]
        print(f"│ {n:>7} {int(statistics.median(jetons[n])):>8} "
              f"{statistics.median(valeurs):>9.2f} {min(valeurs):>7.2f} "
              f"{max(valeurs):>7.2f}")
    print("└────────────────────────────────────────────────────────────────")
    if not mesure["tokenizer_exact"]:
        print()
        print("⚠️  Le compte de tokens est HEURISTIQUE ici (tokenizer réel absent) :")
        print("    la colonne `tokens` est indicative, la colonne latence non.")
    print()
    print("⚠️  Le palier 0 n'a rien à faire varier : il rejoue le même préfixe à")
    print("    chaque passe, donc il est CACHÉ dès la 2ᵉ, contrairement aux autres.")
    print("    La pente se lit entre paliers NON NULS.")
    print()
    print("    Latence croissante ⇒ le prefill des schémas se paie.")
    print("    Colonne plate ⇒ les 4,06 s du 2026-08-01 venaient d'autre chose.")


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
    parser.add_argument(
        "--balayage", action="store_true",
        help="Mesure la latence à 0/17/34/69 outils pour isoler le prefill des schémas",
    )
    parser.add_argument("--json", type=str, default=None, metavar="FICHIER")
    args = parser.parse_args()

    print(f"→ backend {config.BACKEND} · {config.LLM_BASE_URL} · modèle demandé "
          f"« {config.LLM_MODEL} » · {len(TOOLS)} outils\n")

    try:
        mesure = balayer(args.passes) if args.balayage else mesurer(args.passes)
    except MesureImpossible as exc:
        print(f"✗ MESURE IMPOSSIBLE — {exc}", file=sys.stderr)
        print("  (aucun chiffre produit : ce n'est pas un résultat lent, c'est "
              "une absence de résultat)", file=sys.stderr)
        return 1

    if args.balayage:
        _resume_balayage(mesure)
    else:
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

#!/usr/bin/env python3
"""Contrôle : les schémas d'outils sont-ils VRAIMENT payés, ou jetés en route ?

Sonde de VALIDITÉ pour `mesure_plancher_accueil.py --bascule`, pas une mesure de
plus. Elle répond à une objection qui, si elle tenait, annulerait la conclusion
de ce mode.

Le run `--bascule` du 2026-08-01 rend ~0,23 s sur les trois phases, `tools` dans
la charge à chaque appel. Deux lectures incompatibles produisent le MÊME chiffre :

    A. le cache de préfixe est touché  ⇒ la bascule ne re-paie pas le prefill,
                                         la conclusion tient ;
    B. le gateway ignore le champ `tools` ⇒ on n'a jamais mesuré les schémas, et
                                         l'écart nul ne dit rien du tout.

Un écart nul est indiscernable entre les deux tant qu'on ne montre pas que le
prefill est PAYABLE dans ce run — c'est le mode de défaillance dominant du dépôt
(un garde-fou incapable de rougir est indiscernable d'un garde-fou vert).

Trois appels, dans le même run, qui séparent A de B :

    froid_69   69 outils, préfixe UNIQUE (horodaté) → cache impossible
    chaud_69   le même appel rejoué                 → doit s'effondrer
    froid_0    0 outil, préfixe unique              → le sol, sans schémas

Lecture :
    froid_69 ≫ froid_0  ⇒ les schémas sont payés (A). Le `--bascule` mesurait
                          bien 69 outils, et ses 0,23 s sont des cache-hits.
    froid_69 ≈ froid_0  ⇒ `tools` est jeté (B). Le `--bascule` ne prouve rien,
                          et il faut trouver où le champ se perd avant de
                          conclure quoi que ce soit sur le cache.

Le compte de `prompt_tokens` tranche indépendamment de toute latence : il est
rendu par le backend, et il ne peut pas être élevé si les schémas n'ont pas été
lus. C'est lui le juge, la latence n'est que la conséquence.

Usage :
    python scripts/controle_prefill_outils.py

Codes de sortie — « je n'ai pas pu contrôler » n'est PAS « j'ai contrôlé, c'est
bon » :
    0 = contrôle effectué (le verdict A/B est dans la sortie, pas dans le code)
    1 = contrôle impossible (backend injoignable, alias inconnu, RAM refusée)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from tools.registry import TOOLS

# Assez pour que le backend produise une réponse, assez peu pour que la
# génération ne pèse rien devant le prefill — c'est le prefill qu'on mesure.
_MAX_TOKENS = 4


class ControleImpossible(RuntimeError):
    """Le backend n'a pas répondu de quoi contrôler. Distinct d'un contrôle négatif."""


def _appel(
    client: httpx.Client, contenu: str, outils: list[dict] | None
) -> tuple[float, dict[str, Any]]:
    """Un aller-retour chronométré. Retourne (secondes, usage)."""
    charge: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": contenu}],
        "max_tokens": _MAX_TOKENS,
    }
    if outils:
        charge["tools"] = outils

    t0 = time.monotonic()
    reponse = client.post("/chat/completions", json=charge)
    ecoule = time.monotonic() - t0

    if reponse.status_code != 200:
        raise ControleImpossible(
            f"HTTP {reponse.status_code} — {reponse.text[:200]}"
        )
    return ecoule, (reponse.json().get("usage") or {})


def controler() -> dict[str, Any]:
    entetes = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        entetes["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    with httpx.Client(
        base_url=config.LLM_BASE_URL.rstrip("/"),
        headers=entetes,
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
    ) as client:
        try:
            # Amorçage : absorbe le réveil du gateway. Jamais lu — sans lui, le
            # premier bras porterait un chargement de modèle et passerait pour un
            # prefill (la confusion que `--bascule` évite déjà par ses phases).
            _appel(client, "amorçage, ignore cet appel", None)

            # Préfixe unique par horodatage : le cache ne peut pas l'avoir vu. Sans
            # ça, `froid_69` rejouerait un préfixe déjà servi et rendrait un
            # cache-hit — exactement le chiffre qu'on cherche à distinguer.
            marque = f"controle-{time.time_ns()}"

            f69, u69 = _appel(client, f"{marque} dis ok", TOOLS)
            c69, _ = _appel(client, f"{marque} dis ok", TOOLS)
            f0, u0 = _appel(client, f"{marque}-sans dis ok", None)
            c0, _ = _appel(client, f"{marque}-sans dis ok", None)
        except httpx.HTTPError as exc:
            raise ControleImpossible(
                f"{config.LLM_BASE_URL} injoignable ({type(exc).__name__})."
            ) from exc

    return {
        "mode": "controle_prefill_outils",
        "backend": config.BACKEND,
        "base_url": config.LLM_BASE_URL,
        "model_configured": config.LLM_MODEL,
        "nb_outils": len(TOOLS),
        "froid_69_s": round(f69, 3),
        "chaud_69_s": round(c69, 3),
        "froid_0_s": round(f0, 3),
        "chaud_0_s": round(c0, 3),
        "prompt_tokens_69": u69.get("prompt_tokens"),
        "prompt_tokens_0": u0.get("prompt_tokens"),
    }


def _resume(r: dict[str, Any]) -> None:
    print(f"  froid_69  {r['froid_69_s']:6.2f}s   prompt_tokens={r['prompt_tokens_69']}")
    print(f"  chaud_69  {r['chaud_69_s']:6.2f}s")
    print(f"  froid_0   {r['froid_0_s']:6.2f}s   prompt_tokens={r['prompt_tokens_0']}")
    print(f"  chaud_0   {r['chaud_0_s']:6.2f}s")
    print()

    ecart_s = r["froid_69_s"] - r["froid_0_s"]
    tok69, tok0 = r["prompt_tokens_69"], r["prompt_tokens_0"]
    print(f"  écart froid 69-vs-0 : {ecart_s:+.2f}s")

    # Le verdict se pose sur les TOKENS quand ils sont là : ils viennent du
    # backend et ne peuvent pas être élevés sans que les schémas aient été lus.
    # La latence, elle, dépend de la charge de la machine.
    if tok69 is None or tok0 is None:
        print()
        print("✗ CONTRÔLE NON CONCLUANT — le backend n'a pas rendu `usage`.")
        print("  L'écart de latence seul ne sépare pas « schémas payés » de")
        print("  « machine chargée ». Ne pas conclure sur --bascule à partir d'ici.")
        return

    print(f"  écart tokens        : {tok69 - tok0:+d}")
    print()
    if tok69 > tok0 * 10:
        print(f"✅ LECTURE A — les {r['nb_outils']} schémas sont bien payés.")
        print(f"   {tok69} tokens de prompt contre {tok0} sans outils : le champ")
        print("   `tools` est reçu et compté. Un appel rejoué s'effondre")
        print(f"   ({r['froid_69_s']:.2f}s → {r['chaud_69_s']:.2f}s), donc le cache de")
        print("   préfixe existe et porte les schémas.")
        print("   ⇒ Le run --bascule mesurait bien 69 outils : sa conclusion tient.")
    else:
        print("✗ LECTURE B — `tools` semble ignoré par le backend.")
        print(f"   {tok69} tokens avec outils contre {tok0} sans : les schémas")
        print("   n'ont pas été lus. Le run --bascule n'a mesuré aucun prefill")
        print("   d'outils, et son écart nul ne dit RIEN du cache.")
        print("   ⇒ Trouver où le champ se perd avant de conclure.")


def main() -> int:
    print(
        f"→ backend {config.BACKEND} · {config.LLM_BASE_URL} · modèle demandé "
        f"« {config.LLM_MODEL} » · {len(TOOLS)} outils\n"
    )
    try:
        releve = controler()
    except ControleImpossible as exc:
        print(f"✗ CONTRÔLE IMPOSSIBLE — {exc}", file=sys.stderr)
        print(
            "  (aucun chiffre produit : ce n'est pas un contrôle négatif, c'est "
            "une absence de contrôle)",
            file=sys.stderr,
        )
        return 1
    _resume(releve)
    return 0


if __name__ == "__main__":
    sys.exit(main())

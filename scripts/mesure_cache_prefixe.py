#!/usr/bin/env python3
"""Un octet changé dans le prompt système invalide-t-il TOUT le préfixe en cache ?

Trois appels `max_tokens=1` au gateway, schémas d'outils rendus uniques par un
nonce (cache froid garanti) :

    1 froid                     système S1
    2 fin du système changée    S1 dont seul le DERNIER token diffère
    3 rejeu exact de 2

Lecture : `cached_tokens` (rendu par mlx_lm) est le juge, la latence n'est que la
conséquence. Si l'appel 2 rend cached≈0, tout ce qui varie d'un message à l'autre
dans le prompt système (compteur, skills, retrieval) fait recalculer les schémas
d'outils depuis le token 0, à chaque message.

Mesuré le 2026-09-27 (brain, 69 outils + nonce) : 1 → cached=0, 5,34 s ;
2 → cached=0, 11,15 s ; 3 → cached=11 517/11 518, 0,55 s.

Codes de sortie : 0 = mesure faite (le verdict est dans la sortie), 1 = mesure
impossible (gateway injoignable, alias inconnu, RAM refusée).
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from tools.registry import TOOLS

URL = config.MLX_BASE_URL.rstrip("/") + "/chat/completions"
MODELE = config.MLX_MODEL


def main() -> int:
    nonce = uuid.uuid4().hex
    outils = [{"type": "function", "function": {
        "name": "nonce_" + nonce[:8], "description": f"marqueur {nonce}",
        "parameters": {"type": "object", "properties": {}}}}, *TOOLS]
    base = "Tu es Klody, agent de code local. " * 40

    def appel(etiquette: str, systeme: str) -> None:
        corps = {
            "model": MODELE, "max_tokens": 1, "temperature": 0, "tools": outils,
            "messages": [{"role": "system", "content": systeme},
                         {"role": "user", "content": "bonjour"}],
            "chat_template_kwargs": {"enable_thinking": False},
        }
        t = time.perf_counter()
        r = httpx.post(URL, json=corps, timeout=300)
        dt = time.perf_counter() - t
        r.raise_for_status()
        u = r.json().get("usage", {})
        cache = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
        print(f"{etiquette:<30} prompt={u.get('prompt_tokens')} cached={cache} latence={dt:.2f}s",
              flush=True)

    try:
        appel("1 froid", base + "\nRequêtes : 1")
        appel("2 fin du système changée", base + "\nRequêtes : 2")
        appel("3 rejeu exact de 2", base + "\nRequêtes : 2")
    except httpx.HTTPError as exc:
        print(f"mesure impossible : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

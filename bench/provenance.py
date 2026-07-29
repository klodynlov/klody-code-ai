"""Provenance d'un run : quelle configuration a produit ces chiffres.

Sans ça, deux fichiers de résultats sont indistinguables — `--label` porte
l'information à la main, donc mal. Or l'usage principal du bench est justement de
comparer deux configurations : un run non identifié n'est pas exploitable.

**Le cas gateway.** En mode `:8090`, `.env` ne contient qu'un ALIAS (`brain`) : le
modèle réellement chargé n'est connu que du resolver du gateway, et son
`/v1/models` ne peut pas servir de source de vérité (celui de `mlx_lm.server` liste
tout le cache HF, pas le modèle chargé — cf. `start-local-ai.sh`). On résout donc
l'alias comme le fait le pré-vol du nightly : une complétion d'un token, dont la
réponse OpenAI porte le `model` effectivement servi. C'est la seule lecture fiable,
et elle coûte un token.

La résolution est best-effort : injoignable, elle laisse `*_served` à None plutôt
que de faire échouer le bench.
"""
from __future__ import annotations

from typing import Any

import httpx


def resolve_served_model(
    base_url: str,
    model: str,
    api_key: str = "",
    timeout: float = 30.0,
) -> str | None:
    """Retourne l'id du modèle réellement servi derrière `model`, ou None.

    `model` est un alias en mode gateway, un id HF en mode autonome. Dans les deux
    cas la réponse OpenAI porte le nom effectif, ce qui rend la lecture uniforme.
    """
    if not base_url or not model:
        return None

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        served = response.json().get("model")
    except Exception:
        # Best-effort : un bench doit tourner même si la sonde échoue.
        return None

    return served if isinstance(served, str) and served else None


def describe_config(resolve: bool = True) -> dict[str, Any]:
    """Photographie la configuration LLM active.

    `resolve=False` coupe les appels réseau (tests, `--dry-run`) : on ne garde que
    ce que `.env` et `config.py` savent déjà.
    """
    import config

    described: dict[str, Any] = {
        "backend": config.BACKEND,
        "base_url": config.LLM_BASE_URL,
        "model_configured": config.LLM_MODEL,
        "model_served": None,
        "code_model_configured": config.CODE_MODEL,
        "code_model_served": None,
        "context_window": config.CONTEXT_WINDOW,
    }

    if not resolve:
        return described

    described["model_served"] = resolve_served_model(
        config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY
    )
    if config.CODE_MODEL:
        described["code_model_served"] = resolve_served_model(
            config.LLM_BASE_URL, config.CODE_MODEL, config.LLM_API_KEY
        )
    return described


def describe_short(meta: dict[str, Any] | None) -> str:
    """Résumé d'une ligne pour l'affichage, tolérant aux métadonnées absentes.

    Les runs antérieurs à l'introduction des métadonnées n'en ont pas : ils doivent
    rester lisibles plutôt que de faire planter un rapport.
    """
    if not meta:
        return "configuration inconnue (run sans métadonnées)"

    served = meta.get("model_served")
    configured = meta.get("model_configured")
    if served and configured and served != configured:
        model = f"{configured} → {served}"
    else:
        model = served or configured or "?"

    backend = meta.get("backend") or "?"
    return f"{model} · backend={backend}"

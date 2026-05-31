"""RAG Proxy — middleware entre Aider/Klody et mlx-lm.

Écoute sur localhost:8081 (OpenAI-compatible).
Enrichit le system prompt avec des chunks LibraryBrain + skills domaine.
Route le mode de raisonnement (think / no_think) selon la nature de la tâche.
Transmet la requête enrichie à mlx-lm sur localhost:8080.

Contrôle du raisonnement (Qwen3 / Qwen3.6 / autres modèles "thinking") :
- primaire — champ `chat_template_kwargs.enable_thinking` injecté dans le body
  forwardé ; respecté par les builds récents de mlx-lm qui propagent les kwargs
  au gabarit de chat.
- fallback — tag inline `/think` ou `/no_think` ajouté au message system (les
  modèles Qwen3 le détectent dans le dernier prompt système).
Les deux sont posés en même temps (belt-and-braces) : si l'un est ignoré par le
build de mlx-lm en place, l'autre prend le relais.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

# Ajouter la racine du projet au path pour importer config
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import LIBRARYBRAIN_URL, SKILLS_DIR  # noqa: E402

# — Configuration ——————————————————————————————————————————————————————————————

MLX_URL = os.getenv("MLX_URL", "http://127.0.0.1:8080")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8081"))
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "2000"))

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "symfony": ["symfony", "doctrine", "twig", "bundle", "entity", "repository", "php"],
    "nextjs":  ["next.js", "nextjs", "next ", "app router", "server component", "vercel", "react"],
    "mlx":     ["mlx", "lora", "quantiz", "fine-tun", "mlx-lm", "apple silicon", "mistral", "llama"],
    "python":  ["python", "pytest", "dataclass", "asyncio", "pydantic", "fastapi", "pip"],
}

# Routage thinking — listes courtes et orthogonales. Premier match gagne.
# Mettre les indices en minuscule, sans accents pré-supposés (on normalise).
_THINK_KEYWORDS: tuple[str, ...] = (
    "plan", "planifie", "planning", "architecture", "design system",
    "refactor", "refactoring", "debug", "stacktrace", "race condition",
    "distille", "distill", "analyse", "audit", "root cause",
    "trade-off", "tradeoff", "compare", "choisir entre",
    "raisonne", "explique pourquoi", "demonstration",
)
_NO_THINK_KEYWORDS: tuple[str, ...] = (
    "complete", "autocomplete", "completion",
    "renomme", "rename", "format", "lint",
    "template", "boilerplate", "snippet", "scaffold",
    "applique le skill", "apply skill", "applique la methode",
    "ecris un test", "ajoute un test", "fix typo",
)

app = FastAPI(title="RAG Proxy", version="1.1.0")

# — Domain detection ————————————————————————————————————————————————————————————


def _detect_domain(text: str) -> str:
    text_lower = text.lower()
    scores = {
        domain: sum(1 for kw in kws if kw in text_lower)
        for domain, kws in DOMAIN_KEYWORDS.items()
    }
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else "python"


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"

# — Thinking routing ————————————————————————————————————————————————————————————


def _normalize(text: str) -> str:
    """Normalisation minimale pour le matching de mots-clés (accents → ASCII)."""
    import unicodedata
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")


def _should_think(messages: list[dict], body: dict) -> bool:
    """Décide si le modèle doit raisonner pour cette requête.

    Priorité :
    1. Override explicite — `body["chat_template_kwargs"]["enable_thinking"]`
       ou `body["thinking"]` (bool). Si présent, on respecte.
    2. Heuristique de mots-clés sur le concat (system + user) normalisé.
       Match `_THINK_KEYWORDS`  → True
       Match `_NO_THINK_KEYWORDS` → False
       Aucun match → False (défaut Klody : mode rapide).
    """
    # 1. Override explicite
    explicit = body.get("chat_template_kwargs", {}).get("enable_thinking")
    if isinstance(explicit, bool):
        return explicit
    explicit2 = body.get("thinking")
    if isinstance(explicit2, bool):
        return explicit2

    # 2. Heuristique
    blob = _normalize(
        " ".join(m.get("content", "") for m in messages if m.get("role") in ("system", "user"))
    )
    for kw in _THINK_KEYWORDS:
        if kw in blob:
            return True
    for kw in _NO_THINK_KEYWORDS:
        if kw in blob:
            return False
    return False


_INLINE_TAG_RE = re.compile(r"(?:^|\s)/(?:think|no_think)\b", flags=re.IGNORECASE)


def _apply_thinking_mode(body: dict, messages: list[dict], think: bool) -> list[dict]:
    """Pose les deux contrôles sur le body et les messages.

    - body : `chat_template_kwargs.enable_thinking = think` (primaire).
    - messages : tag inline `/think` ou `/no_think` en fin de **dernier**
      message system (fallback). Si aucun message system, on en crée un.

    Le tag inline est ré-écrit même s'il existe déjà (le proxy a autorité).
    """
    body.setdefault("chat_template_kwargs", {})
    body["chat_template_kwargs"]["enable_thinking"] = think

    tag = "/think" if think else "/no_think"
    out: list[dict] = []
    last_system_idx: int | None = None
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            last_system_idx = i
        out.append(dict(msg))

    if last_system_idx is None:
        out.insert(0, {"role": "system", "content": tag})
    else:
        cleaned = _INLINE_TAG_RE.sub("", out[last_system_idx].get("content", "")).rstrip()
        out[last_system_idx]["content"] = f"{cleaned}\n\n{tag}".lstrip()

    return out

# — Data sources ————————————————————————————————————————————————————————————————


async def _search_books(query: str, limit: int = 3) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(LIBRARYBRAIN_URL, json={"query": query, "limit": limit})
            resp.raise_for_status()
            data = resp.json()
        # Format LibraryBrain : {answer, sources:[{title,author,page,score}], found}
        if not data.get("found", False):
            return []
        return [{"content": data.get("answer", ""), "sources": data.get("sources", [])}]
    except httpx.ConnectError:
        logger.warning("LibraryBrain unreachable — skipping book search")
        return []
    except httpx.HTTPStatusError as exc:
        logger.warning("LibraryBrain returned {}: {}", exc.response.status_code, LIBRARYBRAIN_URL)
        return []
    except Exception as exc:
        logger.error("search_books unexpected error: {}", exc)
        return []


def _get_skills(domain: str) -> list[dict]:
    path = SKILLS_DIR / f"{domain}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("get_skills('{}') error: {}", domain, exc)
        return []

# — Context builder —————————————————————————————————————————————————————————————


async def _build_context(user_text: str) -> str:
    domain = _detect_domain(user_text)
    logger.info("Detected domain: {}", domain)

    chunks, skills = await asyncio.gather(
        _search_books(user_text, limit=3),
        asyncio.to_thread(_get_skills, domain),
    )

    parts: list[str] = []

    if chunks:
        parts.append("## Extraits de livres pertinents")
        for c in chunks:
            content = c.get("content", "")
            srcs = c.get("sources", [])
            ref = " | ".join(
                f"{s.get('title','?')} — {s.get('author','?')}, p.{s.get('page','?')}"
                for s in srcs
            ) if srcs else "source inconnue"
            parts.append(f"[{ref}]\n{content}")

    if skills:
        parts.append(f"## Conventions {domain}")
        for s in skills[:3]:
            parts.append(f"**{s['title']}**\n{s['content']}")

    if not parts:
        return ""

    raw = "\n\n".join(parts)
    return _truncate_to_tokens(raw, MAX_CONTEXT_TOKENS)


def _inject_context(messages: list[dict], context: str) -> list[dict]:
    if not context:
        return messages

    block = f"<context>\n{context}\n</context>\n\n"
    result: list[dict] = []
    injected = False

    for msg in messages:
        if msg.get("role") == "system" and not injected:
            result.append({**msg, "content": block + msg["content"]})
            injected = True
        else:
            result.append(msg)

    if not injected:
        result.insert(0, {"role": "system", "content": block.rstrip()})

    return result

# — Routes ——————————————————————————————————————————————————————————————————————


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body: dict = await request.json()
    messages: list[dict] = body.get("messages", [])

    user_text = " ".join(
        m.get("content", "") for m in messages if m.get("role") == "user"
    )
    context = await _build_context(user_text)
    messages = _inject_context(messages, context)

    think = _should_think(messages, body)
    messages = _apply_thinking_mode(body, messages, think)
    body["messages"] = messages

    logger.info(
        "Forwarding to mlx-lm — context {} chars, thinking={}",
        len(context),
        think,
    )

    is_stream = body.get("stream", False)
    async with httpx.AsyncClient(timeout=120.0) as client:
        if is_stream:
            async def _stream() -> Any:
                async with client.stream(
                    "POST", f"{MLX_URL}/v1/chat/completions", json=body
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

            return StreamingResponse(_stream(), media_type="text/event-stream")

        resp = await client.post(f"{MLX_URL}/v1/chat/completions", json=body)
        return JSONResponse(content=resp.json(), status_code=resp.status_code)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "rag-proxy", "version": "1.1.0"}


# — Entry point —————————————————————————————————————————————————————————————————

if __name__ == "__main__":
    logger.info("RAG Proxy starting on port {}", PROXY_PORT)
    uvicorn.run(app, host="127.0.0.1", port=PROXY_PORT, log_level="warning")

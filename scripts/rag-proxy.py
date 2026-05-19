"""RAG Proxy — middleware entre Aider et mlx-lm.

Écoute sur localhost:8081 (OpenAI-compatible).
Enrichit le system prompt avec des chunks LibraryBrain + skills domaine.
Transmet la requête enrichie à mlx-lm sur localhost:8080.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

# — Configuration ——————————————————————————————————————————————————————————————

LIBRARYBRAIN_URL = os.getenv("LIBRARYBRAIN_URL", "http://127.0.0.1:8765/ask")
MLX_URL = os.getenv("MLX_URL", "http://127.0.0.1:8080")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8081"))
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "2000"))
SKILLS_DIR = Path(__file__).parent.parent / "skills"

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "symfony": ["symfony", "doctrine", "twig", "bundle", "entity", "repository", "php"],
    "nextjs": ["next.js", "nextjs", "next ", "app router", "server component", "vercel", "react"],
    "mlx": ["mlx", "lora", "quantiz", "fine-tun", "mlx-lm", "apple silicon", "mistral", "llama"],
    "python": ["python", "pytest", "dataclass", "asyncio", "pydantic", "fastapi", "pip"],
}

app = FastAPI(title="RAG Proxy", version="1.0.0")

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

# — Data sources ————————————————————————————————————————————————————————————————


async def _search_books(query: str, limit: int = 3) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(LIBRARYBRAIN_URL, json={"query": query, "limit": limit})
            resp.raise_for_status()
            data = resp.json()
        raw = data if isinstance(data, list) else data.get("results", data.get("chunks", []))
        return raw[:limit]
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
            source = c.get("source", c.get("metadata", {}).get("source", "?"))
            content = c.get("content", c.get("text", ""))
            parts.append(f"[{source}]\n{content}")

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
    body["messages"] = _inject_context(messages, context)

    logger.info("Forwarding to mlx-lm — context {} chars", len(context))

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
    return {"status": "ok", "service": "rag-proxy", "version": "1.0.0"}


# — Entry point —————————————————————————————————————————————————————————————————

if __name__ == "__main__":
    logger.info("RAG Proxy starting on port {}", PROXY_PORT)
    uvicorn.run(app, host="127.0.0.1", port=PROXY_PORT, log_level="warning")

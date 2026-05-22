"""
KlodyAI API Server — FastAPI + WebSocket
Bridge entre l'agent Python et le dashboard Tauri.
"""

import asyncio
import json
import logging
import re
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Chemin vers la racine du projet pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import MEMORY_DIR, MODEL_FALLBACK, MODEL_NAME, OLLAMA_BASE_URL, PROJECT_ROOT, LIBRARYBRAIN_DIR, LIBRARYBRAIN_URL
from agent.memory import ConversationMemory
from agent.orchestrator import Orchestrator
from services import ensure_librarybrain, get_librarybrain_status
from agent.long_term_memory import get_long_term_memory
from agent.memory_extractor import extract_and_save

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_librarybrain(LIBRARYBRAIN_DIR, LIBRARYBRAIN_URL)
    yield


app = FastAPI(title="KlodyAI API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:1420",  # Tauri dev
        "http://localhost:1421",
        "http://localhost:5173",  # Vite dev
        "http://localhost:5174",
        "http://localhost:3000",
        "http://127.0.0.1",
        "http://127.0.0.1:1420",
        "tauri://localhost",       # Tauri production
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

# Sessions actives par WebSocket
_sessions: dict[str, ConversationMemory] = {}

# Stop flag partagé — interrompt le streaming en cours
_stop_flag: list[bool] = [False]


class StopGeneration(Exception):
    pass


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    ollama_ok = False
    model_name = MODEL_NAME
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(OLLAMA_BASE_URL.replace("/v1", "") + "/api/tags")
            if r.status_code == 200:
                ollama_ok = True
                models = [m["name"] for m in r.json().get("models", [])]
            else:
                models = []
    except Exception:
        models = []

    return {
        "ollama": ollama_ok,
        "model": model_name,
        "models": models,
        "project": str(PROJECT_ROOT),
        "librarybrain": get_librarybrain_status(),
    }


# ── Sessions ──────────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions():
    files = sorted(
        MEMORY_DIR.glob("memory_*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    sessions = []
    for f in files[:20]:
        try:
            data = json.loads(f.read_text())
            msgs = [m for m in data.get("messages", []) if m.get("role") not in ("system", "tool")]
            sessions.append({
                "id": data.get("session_id", f.stem.replace("memory_", "")),
                "title": data.get("title", ""),
                "messages": len(msgs),
                "modified": f.stat().st_mtime,
                "preview": msgs[0]["content"][:60] if msgs else "",
            })
        except Exception:
            continue
    return sessions


@app.get("/api/memories")
async def list_memories():
    return get_long_term_memory().list_all()


@app.delete("/api/memories/{key}")
async def delete_memory(key: str):
    result = get_long_term_memory().forget(key)
    return {"ok": "Oublié" in result, "message": result}


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str):
    from fastapi.responses import PlainTextResponse
    f = MEMORY_DIR / f"memory_{session_id}.json"
    if not f.exists():
        return PlainTextResponse("Session introuvable", status_code=404)
    data = json.loads(f.read_text())
    title = data.get("title") or session_id
    msgs = [m for m in data.get("messages", []) if m.get("role") in ("user", "assistant") and m.get("content")]
    lines = [f"# {title}", f"", f"> Session {session_id} · {len(msgs)} messages", f"", "---", ""]
    for m in msgs:
        if m["role"] == "user":
            lines += [f"**Vous :** {m['content']}", ""]
        else:
            lines += [f"**Klody :**", "", m["content"], "", "---", ""]
    md = "\n".join(lines)
    filename = title[:40].replace("/", "-").replace(" ", "_").replace("—", "-") + ".md"
    return PlainTextResponse(md, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/stop")
async def stop_generation():
    _stop_flag[0] = True
    return {"ok": True}


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    memory = ConversationMemory()
    current_model = MODEL_NAME
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    # Envoyer le statut initial
    await ws.send_json({
        "type": "session_init",
        "session_id": memory.session_id,
        "model": current_model,
    })

    async def send_status():
        non_sys = sum(1 for m in memory.messages if m["role"] != "system")
        await ws.send_json({
            "type": "status",
            "session_id": memory.session_id,
            "model": current_model,
            "messages": non_sys,
        })

    def make_event_orchestrator(mem: ConversationMemory, model: str, q: asyncio.Queue):
        """Crée un orchestrateur qui envoie les événements dans la queue."""
        orch = Orchestrator(mem)
        orch.llm.model = model
        return orch

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            if msg["type"] == "chat":
                user_text = msg["content"].strip()
                if not user_text:
                    continue

                # Créer l'orchestrateur avec callbacks via queue
                _stop_flag[0] = False
                orch = _build_streaming_orchestrator(memory, current_model, queue, loop, _stop_flag)

                def run_agent():
                    try:
                        orch.run(user_text)
                    except StopGeneration:
                        pass
                    except Exception as e:
                        asyncio.run_coroutine_threadsafe(
                            queue.put({"type": "error", "content": str(e)}),
                            loop,
                        )
                        asyncio.run_coroutine_threadsafe(
                            queue.put({"type": "done", "session_id": memory.session_id}),
                            loop,
                        )
                        return

                    asyncio.run_coroutine_threadsafe(
                        queue.put({"type": "done", "session_id": memory.session_id}),
                        loop,
                    )
                    # Extraction mémoire en arrière-plan (non-bloquant)
                    threading.Thread(
                        target=_extract_memory_bg,
                        args=(memory.messages, get_long_term_memory()),
                        daemon=True,
                        name="mem-extractor",
                    ).start()

                thread = threading.Thread(target=run_agent, daemon=True)
                thread.start()

                # Relayer les événements au client
                while True:
                    event = await queue.get()
                    await ws.send_json(event)
                    if event["type"] in ("done", "error"):
                        break

                await send_status()

            elif msg["type"] == "model_change":
                current_model = msg["model"]
                await ws.send_json({"type": "model_changed", "model": current_model})

            elif msg["type"] == "session_new":
                memory = ConversationMemory()
                await ws.send_json({
                    "type": "session_init",
                    "session_id": memory.session_id,
                    "model": current_model,
                })

            elif msg["type"] == "session_load":
                sid = msg.get("session_id", "")
                f = MEMORY_DIR / f"memory_{sid}.json"
                if f.exists():
                    memory = ConversationMemory.load_from_file(f)
                    await ws.send_json({
                        "type": "session_loaded",
                        "session_id": memory.session_id,
                        "messages": _export_messages(memory),
                    })
                else:
                    await ws.send_json({"type": "error", "content": f"Session '{sid}' introuvable"})

            elif msg["type"] == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("WebSocket déconnecté")
    except Exception as e:
        logger.error("WebSocket erreur: %s", e)


def _extract_memory_bg(messages: list[dict], lt_memory) -> None:
    """Lance l'extraction mémoire dans un thread background."""
    try:
        facts = extract_and_save(messages, lt_memory)
        if facts:
            logger.info("[API] %d fait(s) mémorisé(s) automatiquement", len(facts))
    except Exception as e:
        logger.warning("[API] Extraction mémoire échouée : %s", e)


def _export_messages(memory: ConversationMemory) -> list[dict]:
    """Exporte les messages affichables (sans system/tool)."""
    return [
        {"role": m["role"], "content": m["content"]}
        for m in memory.messages
        if m["role"] in ("user", "assistant") and m.get("content")
    ]


def _build_streaming_orchestrator(
    memory: ConversationMemory,
    model: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    stop_flag: list[bool] | None = None,
) -> Orchestrator:
    """Crée un Orchestrator patché pour envoyer des événements dans la queue."""
    orch = Orchestrator(memory)
    orch.llm.model = model

    def _put(event: dict) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    def stream_api(messages: list[dict], tools=None) -> tuple[str, Any]:
        """Streaming direct sans Rich — pour l'API server (pas de TTY)."""
        _put({"type": "thinking"})

        params: dict = {
            "model": orch.llm.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.1,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        full_content = ""
        raw_tool_calls: dict = {}

        try:
            stream = orch.llm.client.chat.completions.create(**params)
            for chunk in stream:
                if stop_flag and stop_flag[0]:
                    _put({"type": "stream_end"})
                    raise StopGeneration()
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    full_content += delta.content
                    _put({"type": "token", "content": delta.content})
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        orch.llm._accumulate_tool_call(raw_tool_calls, tc)
        except StopGeneration:
            raise
        except Exception as e:
            _put({"type": "error", "content": str(e)})
            raise

        tool_calls = list(raw_tool_calls.values()) if raw_tool_calls else None

        # Fallback : tool call émis en JSON texte (pur ou mélangé avec du texte)
        if not tool_calls and full_content and tools:
            valid_names = {t["function"]["name"] for t in tools}
            text_part, parsed = orch.llm.extract_mixed_tool_call(full_content, valid_names)
            if parsed:
                tool_calls = parsed
                if text_part:
                    # Garder la partie texte visible, supprimer le JSON
                    _put({"type": "stream_trim", "content": text_part})
                else:
                    _put({"type": "discard_stream"})
                full_content = text_part
                return full_content, tool_calls

        if full_content:
            _put({"type": "stream_end"})

        # Mise à jour compteur tokens
        orch.llm.total_tokens += len(full_content) // 4

        return full_content, tool_calls

    orch.llm.stream_chat = stream_api

    original_execute = orch._execute_tool

    def execute_with_events(tool_name: str, tool_args: dict) -> str:
        _put({"type": "tool_call", "name": tool_name, "args": tool_args})
        result = original_execute(tool_name, tool_args)
        # Limiter la taille du résultat envoyé au LLM pour éviter les contextes géants
        MAX_RESULT = 3000
        truncated = result[:MAX_RESULT] + f"\n… [tronqué, {len(result) - MAX_RESULT} chars supplémentaires]" if len(result) > MAX_RESULT else result
        preview = truncated[:600] + "…" if len(truncated) > 600 else truncated
        _put({"type": "tool_result", "name": tool_name, "content": preview})
        return truncated

    orch._execute_tool = execute_with_events

    return orch


# ── Siri ──────────────────────────────────────────────────────────────────────

_SIRI_SESSION_ID = "siri"
_SIRI_LOCK = threading.Lock()

_MD_BOLD = re.compile(r'\*{1,3}(.+?)\*{1,3}', re.DOTALL)
_MD_CODE_BLOCK = re.compile(r'```[\s\S]*?```')
_MD_INLINE_CODE = re.compile(r'`[^`]+`')
_MD_HEADING = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_MD_BULLET = re.compile(r'^[-*]\s+', re.MULTILINE)
_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_MD_MULTI_NL = re.compile(r'\n{3,}')


def _strip_markdown(text: str) -> str:
    """Simplifie le markdown pour une lecture TTS naturelle par Siri."""
    text = _MD_CODE_BLOCK.sub('', text)
    text = _MD_INLINE_CODE.sub(lambda m: m.group(0)[1:-1], text)
    text = _MD_BOLD.sub(r'\1', text)
    text = _MD_HEADING.sub('', text)
    text = _MD_BULLET.sub('- ', text)
    text = _MD_LINK.sub(r'\1', text)
    text = _MD_MULTI_NL.sub('\n\n', text)
    return text.strip()


def _run_siri_query(query: str) -> str:
    """Exécute la requête de façon synchrone dans un thread dédié.
    Utilise une session persistante 'siri' et MODEL_FALLBACK pour la réactivité.
    """
    with _SIRI_LOCK:
        siri_file = MEMORY_DIR / f"memory_{_SIRI_SESSION_ID}.json"
        if siri_file.exists():
            memory = ConversationMemory.load_from_file(siri_file)
        else:
            memory = ConversationMemory(session_id=_SIRI_SESSION_ID)

        orch = Orchestrator(memory)
        orch.llm.model = MODEL_FALLBACK

        # Remplace stream_chat par une version synchrone sans affichage Rich
        def _sync_chat(messages: list[dict], tools=None) -> tuple[str, Any]:
            params: dict = {
                "model": orch.llm.model,
                "messages": messages,
                "stream": False,
                "temperature": 0.1,
            }
            if tools:
                params["tools"] = tools
                params["tool_choice"] = "auto"

            completion = orch.llm.client.chat.completions.create(**params)
            choice = completion.choices[0]
            content: str = choice.message.content or ""

            tool_calls = None
            raw_tcs = choice.message.tool_calls or []
            if raw_tcs:
                tool_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in raw_tcs
                ]
            elif content and tools:
                valid = {t["function"]["name"] for t in tools}
                _, parsed = orch.llm.extract_mixed_tool_call(content, valid)
                if parsed:
                    tool_calls = parsed
                    content = ""

            orch.llm.total_tokens += len(content) // 4
            return content, tool_calls

        orch.llm.stream_chat = _sync_chat
        # Supprime l'affichage Rich des outils (on garde l'exécution)
        orch._execute_and_display = lambda name, args: orch._execute_tool(name, args)

        try:
            orch.run(query)
        except Exception as e:
            logger.error("[Siri] Erreur agent: %s", e, exc_info=True)
            return "Une erreur s'est produite. Vérifiez que Ollama est démarré."

        last = next(
            (m["content"] for m in reversed(memory.messages)
             if m["role"] == "assistant" and m.get("content")),
            "Je n'ai pas pu répondre.",
        )
        return _strip_markdown(last)


@app.post("/api/siri")
async def siri_post(request: Request):
    """Endpoint pour Siri Shortcut — reçoit {query} et retourne {response}."""
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return {"response": "Question vide.", "session_id": _SIRI_SESSION_ID}
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, _run_siri_query, query)
    return {"response": response, "session_id": _SIRI_SESSION_ID}


@app.get("/api/siri")
async def siri_get(q: str = ""):
    """Endpoint GET pour test rapide : GET /api/siri?q=ta+question"""
    query = q.strip()
    if not query:
        return {"response": "Paramètre q manquant.", "session_id": _SIRI_SESSION_ID}
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, _run_siri_query, query)
    return {"response": response, "session_id": _SIRI_SESSION_ID}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "klody-api", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

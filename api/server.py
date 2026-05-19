"""
KlodyAI API Server — FastAPI + WebSocket
Bridge entre l'agent Python et le dashboard Tauri.
"""

import asyncio
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Chemin vers la racine du projet pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import MEMORY_DIR, MODEL_NAME, OLLAMA_BASE_URL, PROJECT_ROOT
from agent.memory import ConversationMemory
from agent.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

app = FastAPI(title="KlodyAI API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sessions actives par WebSocket
_sessions: dict[str, ConversationMemory] = {}


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
                "messages": len(msgs),
                "modified": f.stat().st_mtime,
                "preview": msgs[0]["content"][:60] if msgs else "",
            })
        except Exception:
            continue
    return sessions


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    memory = ConversationMemory()
    current_model = MODEL_NAME
    loop = asyncio.get_event_loop()
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
                orch = _build_streaming_orchestrator(memory, current_model, queue, loop)

                def run_agent():
                    try:
                        orch.run(user_text)
                        asyncio.run_coroutine_threadsafe(
                            queue.put({"type": "done", "session_id": memory.session_id}),
                            loop,
                        )
                    except Exception as e:
                        asyncio.run_coroutine_threadsafe(
                            queue.put({"type": "error", "content": str(e)}),
                            loop,
                        )

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
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    full_content += delta.content
                    _put({"type": "token", "content": delta.content})
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        orch.llm._accumulate_tool_call(raw_tool_calls, tc)
        except Exception as e:
            _put({"type": "error", "content": str(e)})
            raise

        tool_calls = list(raw_tool_calls.values()) if raw_tool_calls else None

        # Fallback : tool call émis en JSON texte
        if not tool_calls and full_content and tools:
            valid_names = {t["function"]["name"] for t in tools}
            parsed = orch.llm._parse_text_tool_calls(full_content, valid_names)
            if parsed:
                tool_calls = parsed
                full_content = ""
                _put({"type": "discard_stream"})
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")

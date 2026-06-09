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
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Chemin vers la racine du projet pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from agent import preview_errors
from agent.approval import requires_approval
from agent.long_term_memory import get_long_term_memory
from agent.memory import ConversationMemory
from agent.memory_extractor import extract_and_save
from agent.orchestrator import Orchestrator
from services import ensure_librarybrain, get_librarybrain_status
from tools.skills import delete_skill, load_skills

from api import metrics as _metrics

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pré-démarre le serveur d'aperçu (best-effort, HORS event loop) pour que :8899
    # soit debout dès le boot de l'API. Sinon il ne repart qu'au 1er appel d'outil
    # preview, et tout onglet d'aperçu déjà ouvert reste en « connection refused »
    # (le serveur est un thread démon DANS ce process, donc tué à chaque restart API).
    try:
        from tools.preview import _ensure_server
        await asyncio.get_running_loop().run_in_executor(None, _ensure_server)
    except Exception as exc:  # ne JAMAIS bloquer le boot de l'API pour la preview
        logger.warning("[Preview] pré-démarrage ignoré: %s", exc)
    ensure_librarybrain(config.LIBRARYBRAIN_DIR, config.LIBRARYBRAIN_URL)
    yield
    with suppress(Exception):  # arrêt propre (SIGTERM uvicorn ; pas sur kickstart -k)
        from tools.preview import _stop_server
        _stop_server()


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
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(config.OLLAMA_BASE_URL.replace("/v1", "") + "/api/tags")
            if r.status_code == 200:
                ollama_ok = True
                models = [m["name"] for m in r.json().get("models", [])]
            else:
                models = []
    except Exception:
        models = []

    # Backend MLX status si BACKEND=mlx
    mlx_ok = False
    if config.BACKEND == "mlx":
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(config.MLX_BASE_URL.replace("/v1", "") + "/v1/models")
                if r.status_code == 200:
                    mlx_ok = True
                    models = [m["id"] for m in r.json().get("data", [])]
        except Exception:
            pass

    # Klody MCP server status (port 8083)
    mcp_active = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get("http://127.0.0.1:8083/mcp", headers={"Accept": "text/event-stream"})
            mcp_active = r.status_code in (200, 405, 406)  # 406 = Not Acceptable mais up
    except Exception:
        pass

    # Conventions et erreurs récurrentes (Roadmap v2 #8)
    project_info = _load_project_info()

    return {
        "ollama": ollama_ok,
        "model": config.LLM_MODEL,
        "models": models,
        "project": str(config.PROJECT_ROOT),
        "librarybrain": get_librarybrain_status(),
        # v2 fields
        "backend": config.BACKEND,
        "backend_active": mlx_ok if config.BACKEND == "mlx" else ollama_ok,
        "mcp_server_active": mcp_active,
        "project_info": project_info,
    }


def _load_project_info() -> dict:
    """Lit .klody/conventions.json et errors.json sur le PROJECT_ROOT."""
    info: dict = {"conventions": [], "recurrent_errors": [], "workdir": str(config.PROJECT_ROOT)}
    try:
        from agent.conventions import ConventionDetector
        from agent.error_memory import ErrorMemory
        det = ConventionDetector(config.PROJECT_ROOT)
        report = det.detect()
        info["conventions"] = [
            {"name": c.name, "value": c.value, "evidence": c.evidence, "confidence": c.confidence}
            for c in report.conventions
        ]
        info["workdir"] = report.workdir
        em = ErrorMemory(workdir=config.PROJECT_ROOT)
        info["recurrent_errors"] = [
            {"signature": sig, "count": n} for sig, n in em.recurrent(min_count=2)
        ]
    except Exception as exc:
        logger.debug("project_info load failed: %s", exc)
    return info


# ── Sessions ──────────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions():
    files = sorted(
        config.MEMORY_DIR.glob("memory_*.json"),
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


@app.post("/api/memories")
async def add_memory(request: Request):
    """Enregistre un fait (commande /remember de l'UI). {key, content, category?}."""
    body = await request.json()
    key = (body.get("key") or "").strip()
    content = (body.get("content") or "").strip()
    category = (body.get("category") or "context").strip()
    if not key or not content:
        return {"ok": False, "message": "key et content sont requis."}
    if category not in ("user", "project", "preference", "context"):
        category = "context"
    result = get_long_term_memory().remember(key, content, category)
    return {"ok": not result.startswith("ERREUR"), "message": result}


@app.delete("/api/memories/{key}")
async def delete_memory(key: str):
    result = get_long_term_memory().forget(key)
    return {"ok": "Oublié" in result, "message": result}


# ── Skills (compétences persistantes) ───────────────────────────────────────────

@app.get("/api/skills")
async def list_skills_route():
    """Liste les compétences enregistrées (commande /skills de l'UI)."""
    return load_skills()


@app.delete("/api/skills/{slug}")
async def delete_skill_route(slug: str):
    """Supprime une compétence par son slug."""
    result = delete_skill(slug)
    return {"ok": "supprimée" in result, "message": result}


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str):
    from fastapi.responses import PlainTextResponse
    f = config.MEMORY_DIR / f"memory_{session_id}.json"
    if not f.exists():
        return PlainTextResponse("Session introuvable", status_code=404)
    data = json.loads(f.read_text())
    title = data.get("title") or session_id
    msgs = [m for m in data.get("messages", []) if m.get("role") in ("user", "assistant") and m.get("content")]
    lines = [f"# {title}", "", f"> Session {session_id} · {len(msgs)} messages", "", "---", ""]
    for m in msgs:
        if m["role"] == "user":
            lines += [f"**Vous :** {m['content']}", ""]
        else:
            lines += ["**Klody :**", "", m["content"], "", "---", ""]
    md = "\n".join(lines)
    filename = title[:40].replace("/", "-").replace(" ", "_").replace("—", "-") + ".md"
    return PlainTextResponse(md, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/files/{name}")
async def download_file(name: str):
    """Sert un artefact généré (Excel, texte, zip…) depuis config.DOWNLOADS_DIR.

    Anti-traversée : le chemin servi n'est JAMAIS construit à partir de l'entrée.
    On réduit `name` à un basename, puis on cherche une correspondance EXACTE
    parmi les fichiers réellement présents dans le dossier de téléchargements ; le
    `Path` passé à FileResponse provient donc de `iterdir()` (de confiance), pas
    de l'utilisateur. Renvoie le binaire en pièce jointe (téléchargement direct).
    """
    from fastapi.responses import FileResponse, PlainTextResponse
    requested = Path(name).name
    base = config.DOWNLOADS_DIR.resolve()
    match = next(
        (p for p in base.iterdir() if p.is_file() and p.name == requested),
        None,
    ) if base.is_dir() else None
    if match is None:
        return PlainTextResponse("Fichier introuvable", status_code=404)
    media = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if match.suffix.lower() == ".xlsx" else "application/octet-stream"
    )
    return FileResponse(match, filename=match.name, media_type=media)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Supprime définitivement le fichier de session."""
    f = config.MEMORY_DIR / f"memory_{session_id}.json"
    if not f.exists():
        return {"ok": False, "message": "Session introuvable"}
    try:
        f.unlink()
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "message": str(e)}


@app.post("/api/sessions/{session_id}/rename")
async def rename_session(session_id: str, request: Request):
    """Renomme une session (écrit le champ `title` dans le JSON)."""
    body = await request.json()
    title = (body.get("title") or "").strip()[:80]
    if not title:
        return {"ok": False, "message": "Titre vide"}
    f = config.MEMORY_DIR / f"memory_{session_id}.json"
    if not f.exists():
        return {"ok": False, "message": "Session introuvable"}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        data["title"] = title
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "title": title}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.post("/api/stop")
async def stop_generation():
    _stop_flag[0] = True
    return {"ok": True}


# ── Config runtime (réglages live, sans redémarrage) ─────────────────────────

# Réglages modifiables à chaud. Clé API → (constante config, type).
# La mutation porte sur `config` ET `agent.orchestrator` : ce dernier a importé
# ces noms au chargement et les recopie en attributs d'instance à chaque requête
# (un nouvel Orchestrator est créé par message), donc l'effet est immédiat dès
# le message suivant. Non persisté : réinitialisé au redémarrage (source = .env).
_CONFIG_KEYS: dict[str, tuple[str, type]] = {
    "router_enabled": ("ROUTER_ENABLED", bool),
    "best_of_n_enabled": ("BEST_OF_N_ENABLED", bool),
    "best_of_n_force": ("BEST_OF_N_FORCE", bool),
    "sandbox_auto_exec": ("SANDBOX_AUTO_EXEC", bool),
    "best_of_n_count": ("BEST_OF_N_COUNT", int),
    "sandbox_timeout": ("SANDBOX_TIMEOUT", int),
    "max_iterations": ("MAX_ITERATIONS", int),
}

_CONFIG_BOUNDS: dict[str, tuple[int, int]] = {
    "BEST_OF_N_COUNT": (2, 8),
    "SANDBOX_TIMEOUT": (1, 120),
    "MAX_ITERATIONS": (1, 100),
}


def _current_config() -> dict:
    return {api: getattr(config, const) for api, (const, _t) in _CONFIG_KEYS.items()}


@app.get("/api/config")
async def get_config():
    return _current_config()


@app.post("/api/config")
async def set_config(request: Request):
    import agent.orchestrator as _orch
    body = await request.json()
    updated: dict = {}
    for api, value in (body or {}).items():
        if api not in _CONFIG_KEYS:
            continue
        const, typ = _CONFIG_KEYS[api]
        try:
            if typ is bool:
                val: Any = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            else:
                val = int(value)
                lo, hi = _CONFIG_BOUNDS.get(const, (None, None))
                if lo is not None:
                    val = max(lo, min(val, hi))
        except (ValueError, TypeError):
            continue
        setattr(config, const, val)
        if hasattr(_orch, const):
            setattr(_orch, const, val)  # propage au module consommateur
        updated[api] = val
    return {"ok": True, "updated": updated, "config": _current_config()}


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _metrics.ws_connections_total.inc()
    _metrics.ws_active.inc()

    memory = ConversationMemory()
    # Modèle actif = celui résolu par config selon BACKEND (ollama / mlx).
    current_model = config.LLM_MODEL
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()
    # Approbations en attente : id → (threading.Event, {"approved": bool}).
    # Le thread orchestrator bloque sur l'Event ; la boucle WS le réveille à
    # réception d'un message approval_response. Portée = cette connexion.
    pending_approvals: dict[str, tuple] = {}
    # Questions interactives en attente : id → (threading.Event, {"answer": str}).
    # Même mécanique que les approbations : l'outil ask_user bloque le tour sur
    # l'Event, la boucle WS le réveille à réception d'un question_response.
    pending_questions: dict[str, tuple] = {}

    # Envoyer le statut initial. Le client (WebSocket natif WKWebView, app
    # packagée) tombe parfois juste après le handshake : sans garde, le
    # WebSocketDisconnect remonte HORS du endpoint en exception ASGI non gérée
    # (le try/finally qui décrémente ws_active est porté par la boucle plus
    # bas, pas par cet envoi) → traceback + fuite du gauge ws_active.
    try:
        await ws.send_json({
            "type": "session_init",
            "session_id": memory.session_id,
            "model": current_model,
        })
    except WebSocketDisconnect:  # pragma: no cover - chemin d'erreur réseau
        _metrics.ws_active.dec()
        return

    # Pousser les conventions + erreurs récurrentes en début de session (v2 #8)
    try:
        info = _load_project_info()
        if info["conventions"]:
            await ws.send_json({
                "type": "conventions_loaded",
                "workdir": info.get("workdir"),
                "conventions": info["conventions"],
            })
        if info["recurrent_errors"]:
            await ws.send_json({
                "type": "recurrent_errors",
                "errors": info["recurrent_errors"],
            })
    except Exception as exc:
        logger.debug("Failed to push project info: %s", exc)

    async def send_status():
        non_sys = sum(1 for m in memory.messages if m["role"] != "system")
        await ws.send_json({
            "type": "status",
            "session_id": memory.session_id,
            "model": current_model,
            "messages": non_sys,
        })

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            if msg["type"] == "chat":
                user_text = msg["content"].strip()
                if not user_text:
                    continue

                # Métriques : démarre le timer + compte la requête
                import time as _time
                _chat_t0 = _time.monotonic()
                _chat_status = "ok"

                # Créer l'orchestrateur avec callbacks via queue
                _stop_flag[0] = False
                orch = _build_streaming_orchestrator(memory, current_model, queue, loop, _stop_flag, pending_approvals, pending_questions)

                # Liaison explicite des variables de boucle (orch/user_text/memory)
                # comme arguments par défaut : chaque thread capture les valeurs de
                # SON itération, pas la dernière (correctness + silence B023).
                def run_agent(orch=orch, user_text=user_text, memory=memory):
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

                # Relayer les événements ET écouter l'inbound en concurrence.
                # Pendant un run, l'UI peut renvoyer une décision d'approbation
                # (approval_response) qu'il faut livrer au thread orchestrator
                # bloqué dessus, ou un message stop. La boucle attend donc le
                # premier des deux : event sortant prêt, ou message entrant.
                # Si la WS se déconnecte, on stoppe l'orchestrator (sinon fantôme).
                send_task = asyncio.ensure_future(queue.get())
                recv_task = asyncio.ensure_future(ws.receive_text())
                try:
                    while True:
                        done, _ = await asyncio.wait(
                            {send_task, recv_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if send_task in done:
                            event = send_task.result()
                            await ws.send_json(event)
                            et = event.get("type")
                            # Métriques : observe les événements qui transitent
                            if et == "tool_start":
                                _metrics.tool_calls_total.labels(
                                    tool=event.get("tool", "unknown")
                                ).inc()
                            elif et == "anti_stall":
                                _metrics.anti_stall_total.inc()
                            elif et == "text_to_action":
                                _metrics.text_to_action_total.inc()
                            elif et == "error":
                                _chat_status = "error"
                            if et in ("done", "error"):
                                break
                            send_task = asyncio.ensure_future(queue.get())
                        if recv_task in done:
                            raw = recv_task.result()  # WebSocketDisconnect propagé ici
                            try:
                                ctrl = json.loads(raw)
                            except (ValueError, TypeError):
                                ctrl = {}
                            ctype = ctrl.get("type")
                            if ctype == "approval_response":
                                entry = pending_approvals.get(ctrl.get("id", ""))
                                if entry:
                                    ev, holder = entry
                                    holder["approved"] = bool(ctrl.get("approved"))
                                    ev.set()
                            elif ctype == "question_response":
                                entry = pending_questions.get(ctrl.get("id", ""))
                                if entry:
                                    ev, holder = entry
                                    # Idempotence : on ignore une réponse en double /
                                    # tardive (l'Event déjà armé) et on coerce None→""
                                    # (un client mal formé pourrait envoyer answer:null,
                                    # str(None) donnerait le littéral "None").
                                    if not ev.is_set():
                                        holder["answer"] = str(ctrl.get("answer") or "")
                                        ev.set()
                            elif ctype == "stop":
                                _stop_flag[0] = True
                            # ping / autres : ignorés tant qu'un run est en cours
                            recv_task = asyncio.ensure_future(ws.receive_text())
                except WebSocketDisconnect:
                    logger.info("WS déconnectée pendant génération → stop_flag set")
                    _stop_flag[0] = True
                    _chat_status = "stopped"
                    _metrics.chat_requests_total.labels(status=_chat_status).inc()
                    _metrics.chat_duration_seconds.observe(_time.monotonic() - _chat_t0)
                    # Re-raise pour sortir proprement de la boucle while True externe
                    raise
                finally:
                    # Annule les tâches encore en vol PUIS attend leur unwind avant de
                    # rendre la main à la boucle externe. cancel() est asynchrone : sans
                    # await, l'ancien receive_text() reste enregistré comme waiter et le
                    # prochain receive_text() de la boucle externe lève "cannot call recv
                    # while another coroutine is already waiting for the next message".
                    # gather(return_exceptions=True) draine aussi les exceptions settlées
                    # (évite "Task exception was never retrieved").
                    for _t in (send_task, recv_task):
                        if not _t.done():
                            _t.cancel()
                    await asyncio.gather(send_task, recv_task, return_exceptions=True)

                _metrics.chat_requests_total.labels(status=_chat_status).inc()
                _metrics.chat_duration_seconds.observe(_time.monotonic() - _chat_t0)
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
                f = config.MEMORY_DIR / f"memory_{sid}.json"
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
        # Filet de sécurité : si une génération était en cours, stoppe-la.
        _stop_flag[0] = True
    except Exception as e:
        logger.error("WebSocket erreur: %s", e)
        _stop_flag[0] = True
    finally:
        _metrics.ws_active.dec()


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
    pending_approvals: dict | None = None,
    pending_questions: dict | None = None,
) -> Orchestrator:
    """Crée un Orchestrator patché pour envoyer des événements dans la queue."""
    orch = Orchestrator(memory)
    orch.llm.model = model

    def _put(event: dict) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    # Permet aux outils bloquants (ex. await_distillation) d'observer le stop.
    if stop_flag is not None:
        orch._stop_check = lambda: bool(stop_flag[0])

    # Permet à l'orchestrateur d'émettre des événements custom vers l'UI
    # (ex. preview_feedback — boucle de correction des previews qui plantent).
    orch._emit = _put

    def stream_api(
        messages: list[dict],
        tools=None,
        token_callback=None,
        temperature: float = 0.1,
        silent: bool = False,
        tool_choice: str = "auto",
        max_tokens: int = 8192,
        enable_thinking: bool = False,
    ) -> tuple[str, Any]:
        """Streaming direct sans Rich — pour l'API server (pas de TTY).

        Signature alignée avec LLMClient.stream_chat.
        max_tokens=8192 par défaut : permet de générer des gros fichiers
        (Three.js avec scène complète peut faire 3-5KB, donc ~1500-2000 tokens
        rien que pour le content de write_file). Avant : MLX coupait à ~500 tokens.
        """
        import time as _t
        t0 = _t.perf_counter()
        if not silent:
            _put({"type": "thinking"})

        if enable_thinking:
            # CoT (Qwen3 brain) précède la réponse et consomme beaucoup de tokens :
            # sans marge élargie, il mange tout le budget et `content` reste vide.
            # Miroir exact de LLMClient.stream_chat (cf. config.THINKING_*).
            max_tokens = max(max_tokens, config.THINKING_MAX_TOKENS)
        params: dict = {
            "model": orch.llm.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},  # tokens réels dans le chunk final
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if enable_thinking:
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        full_content = ""
        raw_tool_calls: dict = {}
        usage = None  # rempli par le chunk final (stream_options.include_usage)

        # Vérification précoce : si l'utilisateur a déjà demandé l'arrêt (WS disconnect,
        # bouton stop, etc.), on évite même de lancer le LLM call (coûteux côté MLX).
        if stop_flag and stop_flag[0]:
            raise StopGeneration()

        try:
            stream = orch.llm.client.chat.completions.create(**params)
            for chunk in stream:
                # Check stop_flag sur CHAQUE chunk, même en mode silent (BoN/router).
                # Le mode silent ne pousse rien à l'UI mais doit pouvoir s'arrêter.
                if stop_flag and stop_flag[0]:
                    if not silent:
                        _put({"type": "stream_end"})
                    raise StopGeneration()
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # CoT (mode thinking) : le brain Qwen3 émet le raisonnement via
                # `delta.reasoning` AVANT le `content`. On le DIFFUSE à l'UI (panneau
                # « Raisonnement… ») au lieu de laisser un placeholder figé ~33 s
                # sans le moindre signe de vie (A/B 08/06 : TTFT jusqu'à 66 s). Le
                # helper renvoie '' hors mode thinking → aucun coût quand off.
                if enable_thinking and not silent:
                    reasoning_delta = orch.llm._delta_reasoning(delta)
                    if reasoning_delta:
                        _put({"type": "reasoning", "content": reasoning_delta})
                if delta.content:
                    full_content += delta.content
                    if not silent:
                        _put({"type": "token", "content": delta.content})
                    if token_callback:
                        token_callback(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        orch.llm._accumulate_tool_call(raw_tool_calls, tc)
        except StopGeneration:
            raise
        except Exception as e:
            if not silent:
                _put({"type": "error", "content": str(e)})
            raise

        tool_calls = list(raw_tool_calls.values()) if raw_tool_calls else None

        # Fallback : tool call émis en JSON texte (pur ou mélangé avec du texte)
        if not tool_calls and full_content and tools:
            valid_names = {t["function"]["name"] for t in tools}
            text_part, parsed = orch.llm.extract_mixed_tool_call(full_content, valid_names)
            if parsed:
                tool_calls = parsed
                if not silent:
                    if text_part:
                        _put({"type": "stream_trim", "content": text_part})
                    else:
                        _put({"type": "discard_stream"})
                full_content = text_part
                return full_content, tool_calls

        if full_content and not silent:
            _put({"type": "stream_end"})
            # Stats par message — tokens RÉELS si le backend renvoie `usage`
            # (mlx_lm le fait via stream_options.include_usage), sinon estimation.
            elapsed = round(_t.perf_counter() - t0, 2)
            if usage is not None:
                completion_toks = getattr(usage, "completion_tokens", 0) or 0
                prompt_toks = getattr(usage, "prompt_tokens", 0) or 0
                total_toks = getattr(usage, "total_tokens", 0) or (prompt_toks + completion_toks)
            else:
                completion_toks = max(1, len(full_content) // 4)
                prompt_toks = 0
                total_toks = completion_toks
            _put({"type": "message_stats", "latency_s": elapsed,
                  "tokens": completion_toks, "prompt_tokens": prompt_toks,
                  "total_tokens": total_toks, "context_window": config.CONTEXT_WINDOW,
                  "model": orch.llm.model})

        # Mise à jour compteur tokens (réel si dispo)
        orch.llm.total_tokens += (
            (getattr(usage, "completion_tokens", 0) or 0) if usage is not None
            else len(full_content) // 4
        )

        return full_content, tool_calls

    orch.llm.stream_chat = stream_api

    original_execute = orch._execute_tool

    _approval_seq = [0]

    def _request_approval(tool_name: str, tool_args: dict) -> bool:
        """Demande la validation de l'UI et BLOQUE le thread orchestrator
        jusqu'à la réponse. Renvoie True (autorisé) / False (refusé).

        Sans canal d'approbation (CLI, tests), on ne bloque pas : comportement
        historique conservé (le garde-fou TTY de terminal.py reste en place).
        """
        if pending_approvals is None:
            return True
        _approval_seq[0] += 1
        approval_id = f"appr-{_approval_seq[0]}"
        ev = threading.Event()
        holder = {"approved": False}
        pending_approvals[approval_id] = (ev, holder)
        _put({
            "type": "approval_request",
            "id": approval_id,
            "name": tool_name,
            "args": tool_args,
            "reason": tool_args.get("reason", ""),
        })
        # Bloque jusqu'à la décision UI ; vérifie le stop toutes les 0,5 s pour
        # pouvoir annuler proprement (déconnexion / bouton stop). Garde-fou
        # anti-zombie à 30 min si l'utilisateur ne répond jamais.
        waited = 0.0
        timeout_s = 1800.0
        while not ev.wait(timeout=0.5):
            waited += 0.5
            if stop_flag is not None and stop_flag[0]:
                pending_approvals.pop(approval_id, None)
                raise StopGeneration()
            if waited >= timeout_s:
                pending_approvals.pop(approval_id, None)
                _put({"type": "approval_timeout", "id": approval_id, "name": tool_name})
                return False
        pending_approvals.pop(approval_id, None)
        return bool(holder["approved"])

    _question_seq = [0]

    def _request_user_choice(question: str, options: list[str], allow_free_text: bool = True) -> str:
        """Ouvre une question interactive côté UI (carte cliquable) et BLOQUE le
        thread orchestrator jusqu'à la réponse. Renvoie le choix (ou texte libre).

        Décalque _request_approval : Event + holder, réveillé par la boucle WS à
        réception d'un question_response. Sans canal (CLI/tests), renvoie ""
        (l'outil ask_user dégrade alors en repli texte)."""
        if pending_questions is None:
            return ""
        _question_seq[0] += 1
        question_id = f"q-{_question_seq[0]}"
        ev = threading.Event()
        holder = {"answer": ""}
        pending_questions[question_id] = (ev, holder)
        _put({
            "type": "question_request",
            "id": question_id,
            "question": question,
            "options": options,
            "allow_free_text": allow_free_text,
        })
        # Bloque jusqu'à la réponse UI ; check stop toutes les 0,5 s ; garde-fou
        # anti-zombie à 30 min si l'utilisateur ne répond jamais.
        waited = 0.0
        timeout_s = 1800.0
        while not ev.wait(timeout=0.5):
            waited += 0.5
            if stop_flag is not None and stop_flag[0]:
                pending_questions.pop(question_id, None)
                raise StopGeneration()
            if waited >= timeout_s:
                pending_questions.pop(question_id, None)
                _put({"type": "question_timeout", "id": question_id})
                return ""
        pending_questions.pop(question_id, None)
        return str(holder["answer"])

    orch._ask_user = _request_user_choice

    def execute_with_events(tool_name: str, tool_args: dict) -> str:
        # Garde-fou humain : les actions à effet de bord requièrent validation.
        # (and court-circuité : _request_approval n'est appelé que si gardé.)
        if requires_approval(tool_name) and not _request_approval(tool_name, tool_args):
            refusal = "Action refusée par l'utilisateur (non validée)."
            _put({"type": "tool_result", "name": tool_name, "content": refusal})
            return refusal
        # ask_user a son propre canal UI (question_request → carte cliquable) : on
        # n'émet PAS de tool_call/tool_result pour lui (sinon doublon + bande passante).
        emit_ui = tool_name != "ask_user"
        if emit_ui:
            _put({"type": "tool_call", "name": tool_name, "args": tool_args})
        result = original_execute(tool_name, tool_args)
        # Limiter la taille du résultat envoyé au LLM pour éviter les contextes géants
        MAX_RESULT = 3000
        truncated = result[:MAX_RESULT] + f"\n… [tronqué, {len(result) - MAX_RESULT} chars supplémentaires]" if len(result) > MAX_RESULT else result
        preview = truncated[:600] + "…" if len(truncated) > 600 else truncated
        if emit_ui:
            _put({"type": "tool_result", "name": tool_name, "content": preview})
        return truncated

    orch._execute_tool = execute_with_events

    # ── v2 events : router / sandbox / best_of_n ─────────────────────────

    # Router : remplace _display_routing pour émettre l'event vers UI
    def display_routing_api(decision, max_iter: int) -> None:
        with suppress(Exception):
            _put({"type": "router_decision", "decision": {
                "difficulty": decision.difficulty,
                "task_type": decision.task_type,
                "max_iterations": max_iter,
                "use_planner": decision.use_planner,
                "use_best_of_n": decision.use_best_of_n,
                "reasoning": decision.reasoning,
            }})
    orch._display_routing = display_routing_api

    # Skills injectés par pertinence (cf. select_skills) → chip UI « skills utilisés »
    def _emit_skills(names: list[str]) -> None:
        if names:
            _put({"type": "skills_used", "skills": names})
    orch._on_skills_selected = _emit_skills

    # Sandbox auto-check : wrap _auto_sandbox_check pour émettre l'event UI.
    def auto_sandbox_with_event(rel_path: str) -> str:
        # Réimplémentation rapide pour intercepter le SandboxResult avant format.
        # Réutilise la résolution multi-racines de l'orchestrator (CLI/API DRY).
        if not getattr(orch, "_sandbox_auto_exec", True):
            return ""
        target = orch._resolve_sandbox_target(rel_path)
        if target is None:
            return ""
        sandbox, rel_cmd, _root = target
        sb_result = sandbox.run(rel_cmd, timeout=getattr(orch, "_sandbox_timeout", 20))
        if not sb_result.success and sb_result.stderr:
            with suppress(Exception):
                orch.error_memory.record(sb_result.stderr, command=" ".join(rel_cmd))
        _put({"type": "sandbox_check", "check": {
            "command": sb_result.command,
            "success": sb_result.success,
            "exit_code": sb_result.exit_code,
            "stdout": sb_result.stdout,
            "stderr": sb_result.stderr,
            "duration_s": sb_result.duration_s,
            "timed_out": sb_result.timed_out,
        }})
        return f"[sandbox auto-check]\n{sb_result.format_for_llm()}"

    orch._auto_sandbox_check = auto_sandbox_with_event

    # Best-of-N : wrap _run_best_of_n pour émettre l'event puis renvoyer le winner
    original_run_bon = orch._run_best_of_n if hasattr(orch, "_run_best_of_n") else None
    if original_run_bon:
        def run_bon_with_event(messages: list[dict]):
            winner, all_cands, reasoning = orch.best_of_n.best(
                messages, tools=orch.tools, user_prompt=orch._current_user_prompt,
            )
            _put({"type": "best_of_n", "result": {
                "winner_idx": winner.idx,
                "reasoning": reasoning,
                "candidates": [
                    {
                        "idx": c.idx,
                        "temperature": c.temperature,
                        "content": c.content,
                        "tool_calls": [{"name": tc["function"]["name"]} for tc in (c.tool_calls or [])],
                        "latency_s": c.latency_s,
                    }
                    for c in all_cands
                ],
            }})
            # Émettre aussi le content du winner comme stream_end pour qu'il s'affiche
            if winner.content:
                _put({"type": "assistant", "content": winner.content})
            return winner.content, winner.tool_calls
        orch._run_best_of_n = run_bon_with_event

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
        siri_file = config.MEMORY_DIR / f"memory_{_SIRI_SESSION_ID}.json"
        if siri_file.exists():
            memory = ConversationMemory.load_from_file(siri_file)
        else:
            memory = ConversationMemory(session_id=_SIRI_SESSION_ID)

        orch = Orchestrator(memory)
        orch.llm.model = config.MODEL_FALLBACK

        # Remplace stream_chat par une version synchrone sans affichage Rich
        def _sync_chat(messages: list[dict], tools=None, **_kwargs) -> tuple[str, Any]:
            # **_kwargs absorbe les options de streaming/thinking que l'orchestrateur
            # passe à stream_chat (token_callback, silent, tool_choice, max_tokens,
            # enable_thinking…) : ce fallback Siri est synchrone et sans CoT, il les
            # ignore. Sans ça, l'ajout d'un kwarg côté orchestrateur casse Siri.
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


# ── Boucle feedback preview (erreurs JS runtime du code généré) ─────────────────

@app.post("/api/preview_error")
async def preview_error(request: Request) -> Response:
    """Beacon de l'overlay de preview : erreurs JS runtime OU ping « chargé OK ».

    Reçu en text/plain (navigator.sendBeacon → pas de preflight CORS). On parse
    en JSON tolérant, on bufferise (agent.preview_errors) et on compte. Best-effort :
    jamais 500 — un beacon raté ne doit pas polluer la console du navigateur.
    L'URL est décodée (location.href encode les accents du nom de fichier).
    """
    try:
        data = json.loads(await request.body() or b"{}")
        url = unquote(str(data.get("url", "")))
        errors = data.get("errors", [])
        if isinstance(errors, list) and errors:
            preview_errors.record(url, errors)
            _metrics.preview_js_errors_total.inc(len(errors))
        elif data.get("ok"):
            preview_errors.mark_loaded(url)
    except Exception as exc:  # pragma: no cover - beacon best-effort
        logger.debug("Beacon preview_error invalide: %s", exc)
    return Response(status_code=204)


# ── Metrics Prometheus ─────────────────────────────────────────────────────────

@app.get("/metrics")
async def prometheus_metrics():
    """Endpoint scrape Prometheus — format texte standard."""
    from fastapi.responses import Response as FastAPIResponse
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return FastAPIResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Health ─────────────────────────────────────────────────────────────────────

async def _probe_url(url: str, timeout: float = 1.5, accept_status: tuple = (200,)) -> bool:
    """Petite probe HTTP — True si reachable et status acceptable."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            return r.status_code in accept_status
    except Exception:
        return False


@app.get("/health")
async def health(response: Response):
    """Probe profonde : OK 200 si tous les backends critiques sont up,
    sinon 503 + détail. Utilisable par k8s liveness/readiness, cron probes, etc.
    """
    # Probes parallèles
    ollama_url = config.OLLAMA_BASE_URL.replace("/v1", "") + "/api/tags"
    mlx_url = config.MLX_BASE_URL.replace("/v1", "") + "/v1/models"
    # Sonder les VRAIS serveurs MCP que l'orchestrateur utilise (KLODY_MCP_SERVERS)
    # plutôt qu'une URL en dur : l'ancienne sonde tapait :8083 = modèle code MLX,
    # qui n'expose pas /mcp → toujours « down » à tort. Les endpoints /mcp
    # répondent 406 à un GET (négociation streamable-http).
    mcp_urls = list(config.MCP_SERVERS.values())

    ollama_p, mlx_p, *mcp_ps = await asyncio.gather(
        _probe_url(ollama_url),
        _probe_url(mlx_url) if config.BACKEND == "mlx" else asyncio.sleep(0, result=None),
        *[_probe_url(u, accept_status=(200, 405, 406)) for u in mcp_urls],
        return_exceptions=True,
    )
    ollama_ok = ollama_p is True
    mlx_ok = mlx_p is True if config.BACKEND == "mlx" else None
    # ok si TOUS les serveurs configurés répondent ; None si aucun n'est configuré.
    mcp_ok = all(p is True for p in mcp_ps) if mcp_urls else None

    # LLM principal selon BACKEND
    llm_ok = mlx_ok if config.BACKEND == "mlx" else ollama_ok

    checks = {
        "llm_backend": "ok" if llm_ok else "down",
        "ollama": "ok" if ollama_ok else "down",
        "mcp": "off" if mcp_ok is None else ("ok" if mcp_ok else "down"),
    }
    if mlx_ok is not None:
        checks["mlx"] = "ok" if mlx_ok else "down"

    # Critique = LLM backend principal. MCP/Ollama secondaire si BACKEND=mlx.
    all_ok = bool(llm_ok)
    if not all_ok:
        response.status_code = 503
    return {
        "status": "ok" if all_ok else "degraded",
        "service": "klody-api",
        "version": "1.0.0",
        "backend": config.BACKEND,
        "checks": checks,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

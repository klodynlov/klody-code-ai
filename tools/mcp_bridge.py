"""Pont client MCP — permet à Klody de CONSOMMER des serveurs MCP externes.

Klody est synchrone (boucle ReAct) ; le client FastMCP est asynchrone. Ce
module fait le pont : chaque appel async est exécuté dans une boucle d'événements
fraîche, sur un thread dédié, pour fonctionner que l'appelant soit dans un thread
sync (CLI) ou sous une boucle asyncio déjà active (API FastAPI).

Convention de nommage des outils : `mcp__{serveur}__{outil}` (standard MCP-sur-LLM,
sans ambiguïté, conforme au pattern OpenAI function-calling ^[a-zA-Z0-9_-]{1,64}$).

Résilience : un serveur injoignable à la découverte est simplement ignoré (zéro
outil ajouté, log d'avertissement) ; une erreur d'appel renvoie un message lisible
au lieu de planter la boucle de l'agent.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "mcp__"
# Timeouts (secondes) pour ne jamais bloquer Klody sur un serveur mort.
_DISCOVER_TIMEOUT = 8.0
_CALL_TIMEOUT = 60.0

# Cache de découverte au niveau processus : l'API recrée un Orchestrator à
# chaque message, on évite ainsi de re-scanner les serveurs (round-trip réseau)
# à chaque requête. Clé = signature de la config serveurs.
_DISCOVERY_CACHE: dict = {}


def _signature(servers: dict) -> tuple | None:
    """Signature hashable et stable d'une config serveurs (pour le cache)."""
    try:
        return tuple(sorted((str(k), str(v)) for k, v in servers.items()))
    except Exception:
        return None


def clear_discovery_cache() -> None:
    """Vide le cache de découverte (force un re-scan au prochain discover)."""
    _DISCOVERY_CACHE.clear()


def _run_async(coro) -> Any:
    """Exécute une coroutine depuis du code synchrone, de façon robuste.

    Utilise un thread dédié avec sa propre boucle d'événements : évite le
    `RuntimeError: asyncio.run() cannot be called from a running event loop`
    quand l'appelant tourne déjà sous une boucle (cas de l'API FastAPI).
    """
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — on re-raise côté appelant
            box["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _result_to_text(result: Any) -> str:
    """Extrait le texte d'un CallToolResult FastMCP de façon robuste."""
    # Contenu structuré (souvent un dict JSON) si disponible
    content = getattr(result, "content", None)
    if content:
        parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(item))
        if parts:
            return "\n".join(parts)
    data = getattr(result, "data", None)
    if data is not None:
        return str(data)
    return str(result)


def _tool_to_openai_schema(server: str, tool: Any) -> dict:
    """Convertit un outil MCP en schéma OpenAI function-calling, nom namespacé."""
    params = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
    desc = getattr(tool, "description", "") or ""
    return {
        "type": "function",
        "function": {
            "name": f"{_PREFIX}{server}__{tool.name}",
            "description": f"[MCP:{server}] {desc}".strip(),
            "parameters": params,
        },
    }


class MCPManager:
    """Gère la connexion à un ou plusieurs serveurs MCP et le routage des appels.

    `servers` : dict {nom_serveur: cible} où `cible` est tout ce que
    `fastmcp.Client` accepte — une URL HTTP (str), un chemin de script (Path/str),
    ou un objet FastMCP (utile pour les tests in-process).
    """

    def __init__(self, servers: dict[str, Any] | None = None):
        self.servers: dict[str, Any] = dict(servers or {})
        self._tools: list[dict] = []
        self._index: dict[str, tuple[str, str]] = {}  # namespaced -> (server, tool)
        self._discovered = False

    # ------------------------------------------------------------------ #
    # Découverte                                                          #
    # ------------------------------------------------------------------ #

    def discover(self, force: bool = False) -> list[dict]:
        """Découvre les outils de tous les serveurs configurés (résilient).

        Retourne la liste des schémas OpenAI. Idempotent (cache) sauf si force.
        """
        if self._discovered and not force:
            return self._tools

        sig = _signature(self.servers)
        if sig is not None and not force and sig in _DISCOVERY_CACHE:
            self._tools, self._index = _DISCOVERY_CACHE[sig]
            self._discovered = True
            return self._tools

        tools: list[dict] = []
        index: dict[str, tuple[str, str]] = {}
        for name, target in self.servers.items():
            try:
                server_tools = _run_async(self._list_tools(target))
            except Exception as exc:  # serveur injoignable / erreur protocole
                logger.warning("[MCP] serveur '%s' injoignable, ignoré : %s", name, exc)
                continue
            for t in server_tools:
                schema = _tool_to_openai_schema(name, t)
                tools.append(schema)
                index[schema["function"]["name"]] = (name, t.name)
            logger.info("[MCP] serveur '%s' : %d outil(s) découvert(s)", name, len(server_tools))

        self._tools = tools
        self._index = index
        self._discovered = True
        if sig is not None:
            _DISCOVERY_CACHE[sig] = (tools, index)
        return tools

    async def _list_tools(self, target: Any) -> list:
        from fastmcp import Client

        async with Client(target, init_timeout=_DISCOVER_TIMEOUT) as client:
            return await client.list_tools()

    # ------------------------------------------------------------------ #
    # Appel                                                               #
    # ------------------------------------------------------------------ #

    def owns(self, tool_name: str) -> bool:
        """True si `tool_name` est un outil MCP géré par ce manager."""
        return tool_name in self._index

    def call(self, tool_name: str, args: dict | None = None) -> str:
        """Appelle un outil MCP namespacé et renvoie son résultat en texte."""
        if tool_name not in self._index:
            return f"ERREUR: outil MCP inconnu '{tool_name}'"
        server, real_name = self._index[tool_name]
        target = self.servers[server]
        try:
            result = _run_async(self._call(target, real_name, args or {}))
        except Exception as exc:
            logger.warning("[MCP] appel '%s' échoué : %s", tool_name, exc)
            return f"ERREUR MCP ({server}): {exc}"
        return _result_to_text(result)

    async def _call(self, target: Any, name: str, args: dict) -> Any:
        from fastmcp import Client

        async with Client(target, init_timeout=_DISCOVER_TIMEOUT) as client:
            return await client.call_tool(
                name, args, timeout=_CALL_TIMEOUT, raise_on_error=False
            )

    # ------------------------------------------------------------------ #
    # Accès                                                               #
    # ------------------------------------------------------------------ #

    @property
    def tools(self) -> list[dict]:
        return self._tools

    def tool_names(self) -> list[str]:
        return list(self._index.keys())

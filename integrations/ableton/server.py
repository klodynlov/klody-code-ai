"""Focused Ableton MCP surface for Klody, backed by the upstream typed tools.

Run with ~/.klody/venvs/ableton-mcp/bin/python, never Klody's main environment. Only local
HTTP and local Live sockets are used. No shell or arbitrary Python tool is added.
"""
import asyncio
import json
import logging
import os
from importlib.metadata import version
from pathlib import Path

# Set these before importing upstream, which reads its port at import time.
os.environ["ABLETON_HOST"] = "127.0.0.1"
os.environ["ABLETON_PORT"] = "9878"
# Import the complete registry so our explicit allowlist is the sole filter.
os.environ.pop("ABLETON_TOOLSETS", None)

if version("mcp-server-ableton-live") != "1.8.0":
    raise RuntimeError("Klody's Live bridge requires mcp-server-ableton-live==1.8.0")

from ableton_live_mcp import tools as _registered_tools  # noqa: F401
from ableton_live_mcp.app import mcp


async def configure():
    enabled = set(json.loads(Path(__file__).with_name("tools.json").read_text()))
    registered = {tool.name for tool in await mcp.list_tools()}
    missing = enabled - registered
    if missing:
        raise RuntimeError(f"Unknown Ableton tools: {sorted(missing)}")
    for name in registered - enabled:
        mcp.remove_tool(name)
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8094
    # Klody discovers and calls via short-lived MCP sessions. The upstream
    # Live connection is serialized by its own lock and survives these sessions.
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    return mcp


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(configure())
    mcp.run(transport="streamable-http")

"""Local HTTP transport for Blender Lab MCP v1.0.0, in its own environment."""
import asyncio
import importlib
import json
import os
from importlib.metadata import version
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from asset_library_tools import TOOL_NAMES as ASSET_TOOLS, register as register_asset_tools

os.environ["BLENDER_MCP_HOST"] = "127.0.0.1"
os.environ["BLENDER_MCP_PORT"] = "9876"

if version("blender-mcp") != "1.0.0" or version("mcp") != "1.30.0":
    raise RuntimeError("Install integrations/blender/requirements.lock in the dedicated environment")

# Keep each socket call below Klody's 60-second tool deadline. A timeout does
# not cancel code already running in Blender: read back before any retry.
from blmcp.tools_helpers import connection

connection._TIMEOUT = 45.0

mcp = FastMCP(
    "klody-blender",
    instructions=(
        "Control the open Blender scene. Inspect before changing it; preserve existing work. "
        "Execute Python only for the requested task. Assign JSON data to result. "
        "If an operation times out, inspect its state before retrying. "
        "Use a small preview or submit a background job for long renders."
    ),
    host="127.0.0.1", port=8095,
    stateless_http=True, json_response=True,
)


async def configure():
    enabled = set(json.loads(Path(__file__).with_name("tools.json").read_text()))
    for name in sorted(enabled - ASSET_TOOLS):
        importlib.import_module(f"blmcp.tools.{name}").register(mcp)
    register_asset_tools(mcp)
    registered = {tool.name for tool in await mcp.list_tools()}
    if missing := enabled - registered:
        raise RuntimeError(f"Unknown Blender tools: {sorted(missing)}")
    # Some upstream modules also register CLI tools that synchronize files.
    # This bridge operates on the current interactive scene only.
    for name in registered - enabled:
        mcp.remove_tool(name)
    return mcp


if __name__ == "__main__":
    asyncio.run(configure())
    mcp.run(transport="streamable-http")

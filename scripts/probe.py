"""Probe synthétique Klody — vérifie qu'un round-trip WS complet fonctionne.

Usage (cron toutes les 15 min recommandé) :
    python scripts/probe.py --url ws://127.0.0.1:8000/api/ws --timeout 30

Exit code :
    0 = OK (round-trip dans les délais)
    1 = LLM non-joignable
    2 = timeout
    3 = erreur protocole

Sortie : JSON sur stdout (utilisable par jq + alertmanager / Slack webhook).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

try:
    import websockets
except ImportError:
    print(
        json.dumps({"status": "error", "reason": "websockets package required: pip install websockets"}),
        flush=True,
    )
    sys.exit(3)


PROBES = [
    "ping",
    "Dis bonjour en une phrase.",
    "Quel est 2+2 ? Réponds juste avec le chiffre.",
]


async def _one_probe(url: str, prompt: str, timeout: float) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        async with websockets.connect(url, open_timeout=5) as ws:
            # Reçoit session_init
            init_raw = await asyncio.wait_for(ws.recv(), timeout=5)
            init = json.loads(init_raw)
            if init.get("type") != "session_init":
                return {"prompt": prompt, "status": "error", "reason": f"expected session_init, got {init.get('type')}"}

            # Drain les messages de boot non-critiques
            await asyncio.sleep(0.1)

            await ws.send(json.dumps({"type": "chat", "content": prompt}))

            # Attend `done` (ou error)
            received_content = False
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except TimeoutError:
                    break
                event = json.loads(raw)
                et = event.get("type")
                if et == "content":
                    received_content = True
                elif et == "done":
                    elapsed = time.monotonic() - t0
                    return {
                        "prompt": prompt,
                        "status": "ok",
                        "elapsed_s": round(elapsed, 2),
                        "had_content": received_content,
                    }
                elif et == "error":
                    return {
                        "prompt": prompt,
                        "status": "error",
                        "reason": event.get("content", "?"),
                    }

            return {"prompt": prompt, "status": "timeout", "elapsed_s": round(time.monotonic() - t0, 2)}

    except (OSError, ConnectionRefusedError) as exc:
        return {"prompt": prompt, "status": "unreachable", "reason": str(exc)}
    except Exception as exc:
        return {"prompt": prompt, "status": "error", "reason": f"{type(exc).__name__}: {exc}"}


async def _run_all(url: str, timeout: float) -> dict[str, Any]:
    results = []
    for p in PROBES:
        r = await _one_probe(url, p, timeout)
        results.append(r)
    statuses = {r["status"] for r in results}
    if statuses == {"ok"}:
        overall = "ok"
    elif "unreachable" in statuses:
        overall = "unreachable"
    elif "timeout" in statuses:
        overall = "timeout"
    else:
        overall = "degraded"
    return {
        "overall": overall,
        "probes": results,
        "url": url,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe synthétique Klody")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/api/ws")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Timeout par probe (défaut 30s)")
    args = parser.parse_args()

    result = asyncio.run(_run_all(args.url, args.timeout))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    return {
        "ok": 0,
        "unreachable": 1,
        "timeout": 2,
        "degraded": 3,
    }.get(result["overall"], 3)


if __name__ == "__main__":
    sys.exit(main())

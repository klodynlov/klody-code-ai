"""Métriques Prometheus pour l'API Klody.

Counters & histograms exposés sur /metrics. Format texte standard scrap-able
par Prometheus, Grafana Agent, OpenTelemetry collector, etc.

L'instrumentation se fait au niveau de l'API server (WS endpoint), pas dans
l'orchestrator — on capte les événements qui transitent par la queue WS, ce
qui évite d'invasiver le code agent. Pour des métriques plus profondes
(par-itération du ReAct), instrumenter directement agent/orchestrator.py.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge

# ── Connexions WS ─────────────────────────────────────────────────────────────

ws_connections_total = Counter(
    "klody_ws_connections_total",
    "Nombre cumulé de connexions WebSocket /api/ws",
)
ws_active = Gauge(
    "klody_ws_active",
    "Connexions WebSocket actives",
)

# ── Chat / requêtes ───────────────────────────────────────────────────────────

chat_requests_total = Counter(
    "klody_chat_requests_total",
    "Requêtes chat envoyées via WS",
    ["status"],  # "ok" | "error" | "stopped"
)
chat_duration_seconds = Histogram(
    "klody_chat_duration_seconds",
    "Durée d'une requête chat complète (de receive à done)",
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)

# ── Tool calls (via événements queue) ─────────────────────────────────────────

tool_calls_total = Counter(
    "klody_tool_calls_total",
    "Tool calls invoqués (depuis événements WS)",
    ["tool"],
)

# ── Anti-stall + text-to-action ────────────────────────────────────────────────

anti_stall_total = Counter(
    "klody_anti_stall_total",
    "Activations du fallback anti-stall (nudge injecté)",
)
text_to_action_total = Counter(
    "klody_text_to_action_total",
    "Activations du fallback text-to-action (extraction code depuis texte)",
)

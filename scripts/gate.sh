#!/usr/bin/env bash
# gate.sh — CONTRAT (C3) : mesure la DISPONIBILITÉ + le PLUMBING de KlodyAI
# (agent de code local ReAct + ~119 outils MCP). Rapide, offline, déterministe :
#   1) /health :8000 → status ok, llm_backend + MCP + deps validés en direct ;
#   2) bench.run --dry-run → le harness ReAct, les 30 tâches (schémas) et le
#      registre d'outils se chargent, 0 exécution LLM (~0.1 s).
# Les gates PROFONDS (pytest 139 fichiers + coverage, bench complet, pip-audit
# --strict) tournent dans la CI GitHub du repo (.github/workflows/ci.yml +
# bench-nightly.yml, runner self-hosted) — pas ré-exécutés ici.
#
# exit 0 = dispo + plumbing OK · 1 = régression · 2 = api injoignable.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3

# 1) Santé live : le stack agent répond.
h="$(curl -sf --noproxy '*' --max-time 8 http://127.0.0.1:8000/health 2>/dev/null)" \
    || { echo "❌ api :8000 injoignable"; exit 2; }
echo "$h" | grep -q '"status":"ok"' \
    || { echo "❌ /health status != ok — $h"; exit 1; }
echo "$h" | grep -q '"mcp":"ok"' \
    || { echo "❌ agrégat MCP != ok — $h"; exit 1; }
echo "- /health OK (llm_backend + MCP + deps)"

# 2) Plumbing : harness bench + schémas de tâches + registre d'outils chargent.
"$PY" -m bench.run --dry-run >/dev/null 2>&1 \
    || { echo "❌ bench.run --dry-run KO (harness / tâches / outils)"; exit 1; }
echo "- bench.run --dry-run OK (harness ReAct + tâches + registre outils)"

echo "✅ KlodyAI gate OK (disponibilité + plumbing)"
exit 0

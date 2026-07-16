#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Klody — orchestrateur d'inférence locale (100% offline, zéro cloud)
# ─────────────────────────────────────────────────────────────────────────────
#  Cerveau (agentique, 512K ctx) : Seed-OSS-36B-Instruct 8bit  → :8080
#  Spécialiste code              : Qwen3-Coder-30B-A3B 8bit     → :8081
#
#  Les deux exposent une API OpenAI-compatible (/v1/chat/completions).
#  Démarrage en arrière-plan avec logs + PID files pour un arrêt propre.
#
#  Usage :
#    ./start-local-ai.sh brain          # cerveau Seed-OSS sur :8080 (défaut Klody)
#    ./start-local-ai.sh code           # spécialiste Qwen3-Coder sur :8081 (Aider lourd)
#    ./start-local-ai.sh both           # les deux (~74 GB RAM — large sur 128 GB)
#    ./start-local-ai.sh stop [brain|code|all]   # défaut: all
#    ./start-local-ai.sh status         # qui tourne + modèle chargé + RAM
#    ./start-local-ai.sh logs [brain|code]       # tail -f du log
#
#  Source de vérité des ids modèles : .env (MLX_MODEL / MLX_CODE_MODEL).
#  Idempotent : ne relance pas un serveur déjà actif sur son port.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Charge .env (exporté) — source de vérité des modèles/ports si défini.
set -a
[[ -f .env ]] && source .env
set +a

BRAIN_MODEL="${MLX_MODEL:-unsloth/Qwen3.6-35B-A3B-MLX-8bit}"
CODE_MODEL="${MLX_CODE_MODEL:-mlx-community/Qwen3-Coder-30B-A3B-Instruct-8bit}"
BRAIN_PORT="${MLX_PORT:-8080}"
CODE_PORT="${MLX_CODE_PORT:-8081}"

# Services satellites affichés en lecture seule dans `status` (jamais démarrés
# ni arrêtés ici) : Ollama (fallback LLM + embeddings bge-m3) et LibraryBrain
# (RAG). On dérive l'hôte:port depuis .env en retirant le suffixe de chemin.
OLLAMA_ROOT="${OLLAMA_BASE_URL:-http://localhost:11434/v1}"; OLLAMA_ROOT="${OLLAMA_ROOT%/v1}"
# LibraryBrain : sonde et dérivation d'URL partagées avec scripts/start-ui.sh —
# les deux copies précédentes avaient dérivé, chacune vers son propre faux vert.
# shellcheck source=scripts/lib/librarybrain-probe.sh
source "$ROOT/scripts/lib/librarybrain-probe.sh"
LB_ROOT="$(lb_base_url)"

LOGDIR="$ROOT/logs"; mkdir -p "$LOGDIR"
RUNDIR="$ROOT/.run"; mkdir -p "$RUNDIR"

# Désactive toute télémétrie sortante des libs HF (contrainte zéro-cloud).
export HF_HUB_DISABLE_TELEMETRY=1
export DISABLE_TELEMETRY=1

c_reset=$'\033[0m'; c_dim=$'\033[2m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'

# Un serveur répond-il déjà sur ce port ?
port_alive() { curl -sf "http://127.0.0.1:$1/v1/models" -o /dev/null 2>/dev/null; }

# Modèle réellement servi sur un port.
# NB : /v1/models de mlx_lm.server liste TOUT le cache HF, pas le modèle chargé.
# On lit donc la ligne de commande du process lié à ce port (source fiable).
loaded_model() {
  ps -Ao args 2>/dev/null | grep "[m]lx_lm.server" | grep -- "--port $1" \
    | sed -n 's/.*--model[ =]\([^ ]*\).*/\1/p' | head -1
}

# Probes des services satellites — lecture seule, ne démarrent/arrêtent rien.
ollama_alive() { curl -sf "$OLLAMA_ROOT/api/tags" -o /dev/null 2>/dev/null; }
ollama_embed_model() {
  curl -sf "$OLLAMA_ROOT/api/tags" 2>/dev/null \
    | grep -o '"name":"[^"]*"' | sed 's/"name":"//; s/"$//' | grep -i bge | head -1
}
# LibraryBrain : voir lb_probe dans scripts/lib/librarybrain-probe.sh. L'ancienne
# `lb_alive` sondait GET / — la page HTML, exemptée du middleware d'auth — et
# restait donc VERTE pendant que tout /api/ répondait 401, c'est-à-dire pendant
# que la bibliothèque était inutilisable pour Klody.

start_one() {
  local name="$1" model="$2" port="$3"
  local log="$LOGDIR/mlx-$name.log" pidf="$RUNDIR/$name.pid"

  if port_alive "$port"; then
    echo "${c_ylw}● $name : déjà actif sur :$port (modèle: $(loaded_model "$port")) — rien à faire${c_reset}"
    return 0
  fi

  echo "${c_dim}→ démarrage $name : $model sur :$port${c_reset}"
  # On réutilise la primitive scripts/start-mlx.sh (qui exec le serveur),
  # backgroundée ici avec log + PID. exec → le PID enregistré est le serveur python.
  nohup "$ROOT/scripts/start-mlx.sh" --model "$model" --port "$port" \
    > "$log" 2>&1 &
  echo $! > "$pidf"

  # Attente du chargement (gros modèle = plusieurs dizaines de sec).
  echo -n "${c_dim}  chargement du modèle "
  for _ in $(seq 1 180); do
    if port_alive "$port"; then echo "${c_grn}prêt${c_reset}"; break; fi
    if ! kill -0 "$(cat "$pidf")" 2>/dev/null; then
      echo "${c_red}échec — voir $log${c_reset}"; tail -n 20 "$log"; return 1
    fi
    sleep 2; echo -n "."
  done
  if port_alive "$port"; then
    echo "${c_grn}● $name prêt : http://127.0.0.1:$port/v1  (log: $log)${c_reset}"
  else
    echo "${c_red}● $name : timeout de chargement — voir $log${c_reset}"; return 1
  fi
}

stop_one() {
  local name="$1" port pidf="$RUNDIR/$1.pid"
  case "$name" in brain) port="$BRAIN_PORT";; code) port="$CODE_PORT";; esac
  if [[ -f "$pidf" ]]; then
    local pid; pid="$(cat "$pidf")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      echo "${c_grn}■ $name arrêté (pid $pid)${c_reset}"
    else
      echo "${c_dim}■ $name : pas de process vivant (pid $pid)${c_reset}"
    fi
    rm -f "$pidf"
  else
    echo "${c_dim}■ $name : aucun PID enregistré${c_reset}"
  fi
}

cmd_status() {
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Klody — inférence locale"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  for entry in "brain:$BRAIN_PORT" "code:$CODE_PORT"; do
    local name="${entry%%:*}" port="${entry##*:}"
    if port_alive "$port"; then
      printf "  %-6s :%s  ${c_grn}UP${c_reset}   %s\n" "$name" "$port" "$(loaded_model "$port")"
    else
      printf "  %-6s :%s  ${c_dim}down${c_reset}\n" "$name" "$port"
    fi
  done
  echo "${c_dim}  RAM serveurs MLX :${c_reset}"
  ps -Ao rss,comm,args 2>/dev/null | grep "[m]lx_lm" \
    | awk '{printf "    %.1f GB  %s\n", $1/1048576, $0}' || echo "    (aucun)"
  echo "${c_dim}  Services satellites :${c_reset}"
  local op="${OLLAMA_ROOT##*:}" lp="${LB_ROOT##*:}"
  if ollama_alive; then
    local em; em="$(ollama_embed_model)"
    printf "    %-12s :%s  ${c_grn}UP${c_reset}   %s\n" "ollama" "$op" "${em:+embed: $em}"
  else
    printf "    %-12s :%s  ${c_dim}down${c_reset}\n" "ollama" "$op"
  fi
  case "$(lb_probe "$LB_ROOT")" in
    "$LB_PROBE_UP")
      printf "    %-12s :%s  ${c_grn}UP${c_reset}\n" "librarybrain" "$lp"
      ;;
    "$LB_PROBE_UNAUTHORIZED")
      # Joignable, mais refusé : ni UP, ni down. Le distinguer est tout l'intérêt
      # — un redémarrage ne répare pas une panne de config.
      printf "    %-12s :%s  ${c_ylw}401 non autorisé${c_reset}\n" "librarybrain" "$lp"
      printf "      ${c_dim}%s${c_reset}\n" "$(lb_unauthorized_detail)"
      ;;
    *)
      printf "    %-12s :%s  ${c_dim}down${c_reset}\n" "librarybrain" "$lp"
      ;;
  esac
}

ACTION="${1:-status}"
case "$ACTION" in
  brain) start_one brain "$BRAIN_MODEL" "$BRAIN_PORT" ;;
  code)  start_one code  "$CODE_MODEL"  "$CODE_PORT" ;;
  both)  start_one brain "$BRAIN_MODEL" "$BRAIN_PORT"; start_one code "$CODE_MODEL" "$CODE_PORT" ;;
  stop)
    case "${2:-all}" in
      brain) stop_one brain ;;
      code)  stop_one code ;;
      all)   stop_one brain; stop_one code ;;
      *) echo "Usage: $0 stop [brain|code|all]"; exit 1 ;;
    esac ;;
  status) cmd_status ;;
  logs)
    case "${2:-brain}" in
      brain) tail -f "$LOGDIR/mlx-brain.log" ;;
      code)  tail -f "$LOGDIR/mlx-code.log" ;;
      *) echo "Usage: $0 logs [brain|code]"; exit 1 ;;
    esac ;;
  *)
    echo "Usage: $0 {brain|code|both|stop [brain|code|all]|status|logs [brain|code]}"
    exit 1 ;;
esac

#!/usr/bin/env bash
# Entraîne un adaptateur LoRA sur les sessions Klody collectées.
#
# Pipeline complet :
#   1. python -m scripts.lora.collect_sessions --min-tools 1 --strip-meta
#   2. ./scripts/lora/train_lora.sh
#   3. (optionnel) ./scripts/lora/fuse_lora.sh  # fusionne dans le modèle de base
#   4. Redémarre MLX avec le modèle fusionné OU avec --adapter-path
#
# Prérequis :
#   - mlx-lm installé (déjà fait pour MLX server)
#   - lora/train.jsonl produit par collect_sessions.py (≥ 50 paires recommandé)
#   - 80-100 Go RAM libre (LoRA 30B sur Apple Silicon)
#
# Usage:
#   ./scripts/lora/train_lora.sh                            # défauts
#   ./scripts/lora/train_lora.sh --iters 500 --batch 2      # custom
#   MODEL=mlx-community/Qwen3-4B-MLX-4bit ./scripts/lora/train_lora.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DATA_DIR="$SCRIPT_DIR/lora"
ADAPTER_DIR="$SCRIPT_DIR/lora/adapters"
TRAIN_FILE="$DATA_DIR/train.jsonl"

MODEL="${MODEL:-mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2}"
ITERS="${ITERS:-300}"
BATCH="${BATCH:-1}"
LR="${LR:-1e-5}"
RANK="${RANK:-8}"

# Parse args supplémentaires
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --iters) ITERS="$2"; shift 2 ;;
    --batch) BATCH="$2"; shift 2 ;;
    --lr)    LR="$2"; shift 2 ;;
    --rank)  RANK="$2"; shift 2 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

if [[ ! -f "$TRAIN_FILE" ]]; then
  echo "❌ $TRAIN_FILE introuvable."
  echo "   Lance d'abord : python -m scripts.lora.collect_sessions --min-tools 1 --strip-meta"
  exit 1
fi

# Vérifier qu'il y a assez de données
N=$(wc -l < "$TRAIN_FILE")
if [[ $N -lt 50 ]]; then
  echo "⚠ Seulement $N paires d'entraînement — trop peu (vise ≥ 50, idéalement 200+)."
  read -r -p "  Continuer quand même ? [y/N] " ans
  [[ "$ans" =~ ^[yY] ]] || exit 0
fi

mkdir -p "$ADAPTER_DIR"

if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Klody LoRA fine-tune"
echo "  Model    : $MODEL"
echo "  Train    : $TRAIN_FILE ($N paires)"
echo "  Adapter  : $ADAPTER_DIR"
echo "  Iters    : $ITERS  Batch : $BATCH  LR : $LR  Rank : $RANK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# mlx_lm.lora attend train.jsonl + valid.jsonl dans --data dir.
# On crée un valid.jsonl symbolique (split rapide via tail/head).
VALID_FILE="$DATA_DIR/valid.jsonl"
N_VALID=$(( N / 10 < 5 ? 5 : N / 10 ))
[[ $N_VALID -gt $N ]] && N_VALID=$N
head -n "$N_VALID" "$TRAIN_FILE" > "$VALID_FILE"

exec python -m mlx_lm lora \
    --model "$MODEL" \
    --train \
    --data "$DATA_DIR" \
    --adapter-path "$ADAPTER_DIR" \
    --iters "$ITERS" \
    --batch-size "$BATCH" \
    --learning-rate "$LR" \
    --lora-parameters "{\"rank\": $RANK, \"scale\": 20.0, \"dropout\": 0.0}" \
    "${EXTRA_ARGS[@]}"

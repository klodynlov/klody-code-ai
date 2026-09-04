#!/usr/bin/env bash
# Lot 1.5 — A/B Best-of-N sur hard + expert, --repeat 3.
# 3 bras : BoN=3 (défaut), BoN=2, BoN=off.
#
# Usage : bash scripts/mesure_best_of_n.sh
# Résultats dans bench/results/ (JSON + MD), comparaison à la fin.
#
# bench.run rend 0 (tout vert) ou 2 (au moins un échec de tâche).
# 1 = exception du harnais → on s'arrête. cf. CLAUDE.md.
set -uo pipefail

PYTHON="${PYTHON:-$(dirname "$0")/../.venv/bin/python}"
BENCH="$PYTHON -m bench.run"

run_bench() {
    local label="$1"
    shift
    echo "  → $label ($(date +%H:%M:%S))"
    env "$@" $BENCH --category hard --repeat 3 --label "${label}_hard"
    local rc_hard=$?
    env "$@" $BENCH --category expert --repeat 3 --label "${label}_expert"
    local rc_expert=$?
    # 0 ou 2 = OK, 1 = harnais cassé → stop
    if [ "$rc_hard" -eq 1 ] || [ "$rc_expert" -eq 1 ]; then
        echo "ERREUR HARNAIS (exit 1) sur $label — abandon"
        exit 1
    fi
}

echo "=== Lot 1.5 — A/B Best-of-N (hard + expert, --repeat 3) ==="
echo "Début : $(date)"
echo ""

echo "--- Bras 1/3 : BEST_OF_N_COUNT=3 (défaut) ---"
run_bench bon3 BEST_OF_N_ENABLED=true BEST_OF_N_COUNT=3

echo "--- Bras 2/3 : BEST_OF_N_COUNT=2 ---"
run_bench bon2 BEST_OF_N_ENABLED=true BEST_OF_N_COUNT=2

echo "--- Bras 3/3 : BEST_OF_N_ENABLED=false ---"
run_bench bonoff BEST_OF_N_ENABLED=false

echo ""
echo "=== Runs terminés — $(date) ==="
echo ""
echo "Fichiers produits :"
ls -lt bench/results/*bon*.json 2>/dev/null | head -10
echo ""
echo "Comparaisons :"
echo ""
echo "--- BoN=3 vs BoN=off ---"
$PYTHON -m bench.compare \
    -a bench/results/*bon3*.json \
    -b bench/results/*bonoff*.json \
    --label-a 'BoN=3' --label-b 'BoN=off' \
    --format md || true
echo ""
echo "--- BoN=3 vs BoN=2 ---"
$PYTHON -m bench.compare \
    -a bench/results/*bon3*.json \
    -b bench/results/*bon2*.json \
    --label-a 'BoN=3' --label-b 'BoN=2' \
    --format md || true
echo ""
echo "--- BoN=2 vs BoN=off ---"
$PYTHON -m bench.compare \
    -a bench/results/*bon2*.json \
    -b bench/results/*bonoff*.json \
    --label-a 'BoN=2' --label-b 'BoN=off' \
    --format md || true

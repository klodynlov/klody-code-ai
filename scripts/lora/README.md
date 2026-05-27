# Klody LoRA — fine-tuning sur tes propres sessions

Pipeline complet pour adapter Qwen3-Coder à **ton style** à partir de tes
sessions Klody passées (logs/memory_*.json).

## Pourquoi

Le modèle de base fait du français correct, du Python générique, et utilise
les outils selon ses habitudes pré-entraînées. Un LoRA fine-tuné sur tes
propres sessions apprend :
- ton style de prompts (vocabulaire, niveau de détail attendu)
- les patterns récurrents de tes projets (frameworks, structure)
- la façon dont tu acceptes / rejettes des propositions
- les conventions implicites de tes repos (déjà détectées en partie par #8)

## Prérequis

- ≥ 50 paires (user → assistant) — minimum statistique pour ne pas overfitter
- Idéalement 200-500 paires pour un signal vraiment exploitable
- 80 Go RAM libre (LoRA 30B Q4 sur Apple Silicon)
- Quelques heures d'entraînement (300 itérations ~= 30 min sur M3 Max,
  proportionnel sur M5)

## Pipeline

```bash
# 1. Collecter tes sessions au format JSONL
python -m scripts.lora.collect_sessions --min-tools 1 --strip-meta
#   → lora/train.jsonl  (~80-200 paires actuellement)

# 2. Entraîner le LoRA (config par défaut : 300 iters, batch 1, rank 8)
./scripts/lora/train_lora.sh
#   → lora/adapters/

# 3. Tester l'adaptateur sans fusion (chargement à chaud côté MLX server)
python -m mlx_lm.server \
    --model mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2 \
    --adapter-path lora/adapters \
    --port 8080

# 4. (Optionnel) Fusion permanente dans le modèle de base
#    Crée un nouveau modèle dans lora/fused/ qu'on peut publier sur HF
python -m mlx_lm fuse \
    --model mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2 \
    --adapter-path lora/adapters \
    --save-path lora/fused
```

## Hyperparams par défaut (point de départ honnête)

| Param | Valeur | Pourquoi |
|---|---|---|
| iters | 300 | Beaucoup d'iters sur peu de data = overfit. 300 est conservateur. |
| batch | 1 | Le modèle 30B saturent la mémoire — pas de batching avec dataset moyen |
| lr | 1e-5 | Plus haut → catastrophic forgetting (le modèle oublie son pré-entraînement) |
| rank | 8 | Compromis qualité / taille adapter (~50 Mo) |

À ajuster selon ta data :
- Plus de data (>200 paires) → augmente iters à 500-1000
- Sessions très répétitives → baisse lr à 5e-6 pour éviter overfit
- Si après train le modèle "perd" son style normal → baisse lr ou rank

## Évaluation honnête

Avant de croire au LoRA, mesure :

```bash
# Bench AVANT (sans adapter)
BACKEND=mlx python -m bench.run --category easy --label before_lora

# Démarre MLX avec l'adapter
KILL et relance MLX avec --adapter-path lora/adapters

# Bench APRÈS
BACKEND=mlx python -m bench.run --category easy --label after_lora

# Compare les deux JSON dans bench/results/
```

Si le LoRA n'améliore pas (ou dégrade) → 3 causes habituelles :
1. Pas assez de data
2. Data trop répétitive (toutes les sessions sont similaires)
3. Hyperparams agressifs (lr trop haut)

## Au lieu de LoRA

Pour des gains plus immédiats avec moins de risque :
- **`save_skill`** : note les patterns que tu réutilises, déjà supporté nativement
- **`.klody/conventions.json`** : auto-détecté par #8
- **`remember_fact`** : préférences inter-sessions, déjà supporté

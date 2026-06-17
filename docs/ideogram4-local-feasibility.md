# Ideogram 4 en local — faisabilité & plan d'install (veille)

**Statut : veille / non installé.** Document de décision. **GATE actif : aucun
poids n'est téléchargé ni aucune dépendance installée tant que l'OK explicite
n'est pas donné.** Décision de cadrage : expérimentation **strictement
non-commerciale** (cf. blocage licence ci-dessous) — **jamais** pour karaibart.fr
ni aucun usage commercial.

## 1. Specs réelles (vérifiées, pas estimées)

Ideogram 4 = modèle text-to-image **9,3 milliards de paramètres**, open-weight,
sorti ~juin 2026. Prompting via interface JSON structurée (layout bounding-box,
palette), rendu texte multilingue, 2K natif.

Repos officiels :
- Poids quantifiés gated : `ideogram-ai/ideogram-4-nf4`, `ideogram-ai/ideogram-4-fp8`
- Inference standalone (hors ComfyUI) : `ideogram-oss/ideogram4` (code Apache-2.0)

### Tailles disque réelles

Mesurées via l'API HF (`size` des `siblings`) sur le miroir bf16
`CalamitousFelicitousness/Ideogram-4-bf16-Diffusers` ; les variantes quantifiées
sont dérivées par ratio de précision (les repos officiels nf4/fp8 sont *gated*,
donc le détail par fichier n'est pas listable sans authentification).

| Composant | bf16 (mesuré) | fp8 (≈ ×0,5) | nf4 (≈ ×0,25) |
|---|---|---|---|
| Transformer (~8,6 B) | **17,3 Go** (4,62+4,63+4,62+3,41) | ~8,6 Go | ~4,3 Go |
| Text encoder (~7,6 B) | **15,2 Go** (4,64+4,60+4,61+1,33) | ~7,6 Go | ~4,0 Go* |
| VAE | **0,16 Go** | 0,16 Go | 0,16 Go |
| **Pipeline total** | **≈ 32,7 Go** | **≈ 16–18 Go** | **≈ 9–12 Go** |

\* le text encoder est souvent conservé en fp8/bf16 même en variante nf4.

### Dépendances & torch

`diffusers`, `transformers`, `accelerate`, `torch` (dtype bf16), `bitsandbytes`
(impliqué par nf4). Version torch exacte non épinglée sur les pages lisibles → **à
vérifier dans le `pyproject.toml` du repo avant tout install** (la tâche évoquait
`torch>=2.11` ; non confirmé par les sources).

## 2. Verdict de faisabilité — Apple Silicon, 128 Go unified

**Binaire, par chemin :**

| Chemin | Faisable sur cette machine ? | Pourquoi |
|---|---|---|
| **nf4** | **NON** | `nf4` = bitsandbytes 4-bit, kernels **CUDA-only**. Le repo tag explicitement « CUDA ». Ne tourne pas sur MPS. (Le « tient sur 24 Go GPU » annoncé = GPU NVIDIA, hors sujet ici.) |
| **fp8** | **NON fiable** | Tag « All » mais le compute fp8 (matmul) sur MPS est absent/non supporté en torch 2.x → fallback CPU ou crash. Pas un chemin sérieux. |
| **bf16 (diffusers)** | **OUI côté mémoire / NON prouvé côté compute** | ~32,7 Go ≪ 128 Go unified = large marge RAM. MAIS dépend du support MPS de l'archi Ideogram4 dans `diffusers` + lenteur probable (2K res sur MPS = minutes/image). |

**Conclusion :** le seul chemin Apple-Silicon-compatible est **bf16 ~33 Go via
diffusers** (PAS le nf4 « 24 Go » mis en avant). Faisable en mémoire ; le support
MPS et la vitesse restent **à valider empiriquement**. Standalone hors ComfyUI =
oui (`python run_inference.py`).

## 3. ⛔ Blocage licence (décisif)

Les poids sont sous **« Ideogram 4 Non-Commercial Model Agreement »** (gated,
acceptation requise) — pour **toutes** les variantes. Le **code** du repo est
Apache-2.0, mais **pas les poids**.

- Contrainte projet = « licence permissive » → **non respectée par les poids**.
- Usage visé = karaibart.fr / univers artiste Klod Ynlov = **commercial** →
  **interdit par la licence**.

➡️ **Décision : usage perso non-commercial uniquement** (R&D locale). Pour les
visuels de karaibart.fr, prévoir une **alternative open permissive** (ex. FLUX.1
[dev/schnell], SDXL, Qwen-Image) dont la licence autorise l'usage commercial —
hors périmètre de ce document.

## 4. Plan d'install minimal (standalone, hors ComfyUI) — NON EXÉCUTÉ

À lancer **seulement après OK explicite**. Cible le chemin bf16/diffusers (le seul
viable sur Apple Silicon), en environnement isolé, pour expérimentation perso.

```bash
# 0. Pré-requis : accepter le gate sur la page HF du modèle + token HF (read).
#    Vérifier d'abord la version torch requise dans le pyproject du repo.

# 1. venv dédié (n'impacte pas l'env Klody)
python3.11 -m venv ~/.venvs/ideogram4 && source ~/.venvs/ideogram4/bin/activate

# 2. code d'inférence officiel (Apache-2.0)
git clone https://github.com/ideogram-oss/ideogram4 ~/ideogram4
cd ~/ideogram4 && pip install -e .   # tire diffusers/transformers/accelerate/torch

# 3. cache HF sur un volume avec la place (~33 Go bf16)
export HF_HOME=~/hf-cache
huggingface-cli login   # token avec accès gate accepté

# 4. inférence locale bf16 sur MPS (PAS nf4 → CUDA-only)
python run_inference.py \
  --prompt "…" --output out.png \
  --quantization none           # bf16 ; valider --device mps dans le script
```

**Garde-fous :**
- venv séparé `~/.venvs/ideogram4` → zéro impact sur les deps de Klody.
- `bitsandbytes` **non requis** par le chemin bf16 (réservé à nf4/CUDA).
- Valider d'abord sur une image basse résolution (512²) pour mesurer la vitesse
  MPS avant de tenter 2K (`--sampler-preset V4_QUALITY_48`).
- Aucune sortie réutilisée commercialement (licence Non-Commercial).

## Sources

- [ideogram-ai/ideogram-4-nf4 — Hugging Face](https://huggingface.co/ideogram-ai/ideogram-4-nf4)
- [ideogram-ai/ideogram-4-fp8 — Hugging Face](https://huggingface.co/ideogram-ai/ideogram-4-fp8)
- [CalamitousFelicitousness/Ideogram-4-bf16-Diffusers — Hugging Face](https://huggingface.co/CalamitousFelicitousness/Ideogram-4-bf16-Diffusers)
- [ideogram-oss/ideogram4 — GitHub (inference standalone, Apache-2.0)](https://github.com/ideogram-oss/ideogram4)
- [ideogram-oss/ComfyUI-Ideogram4 — GitHub](https://github.com/ideogram-oss/ComfyUI-Ideogram4)
- [Ideogram 4.0 Day-0 Support in ComfyUI — blog.comfy.org](https://blog.comfy.org/p/ideogram-4-day-0-support-in-comfyui)

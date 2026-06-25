# Modèles d'image LOCAUX pour karaibart.fr (usage commercial)

**Statut : veille / rien installé (GATE).** Suite au blocage licence d'Ideogram 4
(Non-Commercial, cf. [ideogram4-local-feasibility.md](ideogram4-local-feasibility.md)),
voici les alternatives **open-weight permissives** viables pour un usage **commercial**
(site artiste Klod Ynlov), en **local sur Apple Silicon** (Mac M-series, 128 Go unified).

Critère bloquant = **licence commerciale**. Critère qualité-clé = **rendu de TEXTE
dans l'image** (la force d'Ideogram qu'on remplace). Licences vérifiées de façon
adversariale contre les fichiers officiels.

## Comparatif

| Modèle | Licence | Commercial ? | Texte image | Apple Silicon | Taille bf16 | Verdict |
|---|---|---|---|---|---|---|
| **Qwen-Image** | **Apache-2.0** | ✅ libre, local | **fort** | MLX natif (mflux) | ~41 Go (DiT 20B) ; FP8/GGUF ~20-25 Go | ⭐ **Recommandé #1** |
| **FLUX.1 [schnell]** | **Apache-2.0** | ✅ libre | moyen | MLX natif (mflux, 4-bit prêt) | ~33-34 Go ; 4-bit ~10-12 Go | ⭐ **Recommandé #2** (léger/rapide) |
| SD3.5 Large | Stability Community | ⚠️ libre **< 1 M$ CA** + **attribution obligatoire** | moyen | diffusers+MPS / DrawThings | ~16,5 Go (DiT) ; ~20-30 Go pile | ✔️ OK indé, avec contraintes |
| FLUX.1 [dev] | FLUX [dev] Non-Commercial v2.0 | ⚠️ **sorties** OK commercial, **modèle** non | moyen | MLX natif (mflux) / DrawThings | ~22-24 Go | ✔️ OK pour « mes visuels », pas en service |
| SDXL base 1.0 | CreativeML OpenRAIL++-M | ✅ libre | **faible** | excellent (DrawThings/MLX) | ~6,9 Go | ⚠️ visuels SANS texte seulement |
| Sana (NVIDIA) | NSCL-NC | ❌ **non-commercial** + « NVIDIA Processors » requis | faible | diffusers+MPS (sans objet) | ~3,2 Go | ❌ **Exclu** |

## Recommandation

**Qwen-Image** (primaire) — le **seul** qui coche les deux cases critiques : licence
**Apache-2.0** (commercial local sans seuil ni gating) **ET** rendu de **texte
état-de-l'art** (FR + multilingue), donc le vrai remplaçant de la force d'Ideogram.
Support **MLX natif mature** via [mflux](https://github.com/filipstrand/mflux). Lourd
(~41 Go bf16) mais large dans 128 Go ; variantes FP8/GGUF si besoin de marge.

**FLUX.1 [schnell]** (secondaire) — licence **Apache-2.0** la plus simple, **rapide**
(1-4 steps), MLX natif (poids 4-bit pré-quantisés `argmaxinc/mlx-FLUX.1-schnell-4bit`).
Texte « moyen » → idéal pour visuels génériques/rapides, moins pour la typo dense.

### Nuances licence à connaître (vérifiées)

- **FLUX.1 [dev]** : licence « Non-Commercial » du **modèle**, MAIS la v2.0 (bfl.ai,
  rév. 25/11/2025) autorise explicitement l'usage **commercial des SORTIES** : « You
  may use Output for any purpose (including for commercial purposes) ». Donc générer
  et **vendre/publier ses propres visuels = OK** ; **interdit** = héberger/vendre le
  modèle comme service (licence commerciale séparée requise). ⚠️ Le miroir `LICENSE.md`
  sur HF affiche encore v1.1.1 (ambiguë) → se fier à **bfl.ai**, pas au miroir.
- **SD3.5** : gratuit commercial **uniquement < 1 M$ de CA annuel**, au-delà Enterprise
  License ; **attribution « Powered by Stability AI » obligatoire** sur le site + repo gaté.
- **Sana** : poids NSCL-NC = non-commercial **et** clause « NVIDIA Processors » → double
  blocage sur Mac, **exclu**.

## Plan d'install minimal (mflux, MLX natif) — NON EXÉCUTÉ (GATE)

À lancer seulement après ton OK. Standalone, hors ComfyUI, venv isolé.

```bash
# venv dédié (zéro impact sur l'env Klody)
python3.11 -m venv ~/.venvs/mflux && source ~/.venvs/mflux/bin/activate
pip install mflux                          # port MLX natif de FLUX + Qwen-Image

# Qwen-Image (Apache-2.0, texte fort) — 1re génération télécharge les poids
mflux-generate --model qwen-image \
  --prompt "affiche karaibart, lettrage manuscrit créole, couleurs océan" \
  --steps 20 --quantize 8 --output ~/karaibart/test.png

# FLUX.1 [schnell] (Apache-2.0, rapide) — 4 steps
mflux-generate --model schnell \
  --prompt "pochette single Klod Ynlov, ambiance tropicale" \
  --steps 4 --quantize 4 --output ~/karaibart/schnell.png
```

> Vérifier les noms de modèle exacts dans la doc mflux courante avant de lancer
> (l'API CLI évolue). Valider en 512² d'abord pour mesurer la vitesse, puis monter.

## Sources

- [Qwen/Qwen-Image — HF (Apache-2.0)](https://huggingface.co/Qwen/Qwen-Image) · [LICENSE](https://huggingface.co/Qwen/Qwen-Image/blob/main/LICENSE)
- [black-forest-labs/FLUX.1-schnell — HF (Apache-2.0)](https://huggingface.co/black-forest-labs/FLUX.1-schnell) · [mflux](https://github.com/filipstrand/mflux)
- [stabilityai/stable-diffusion-3.5-large — HF](https://huggingface.co/stabilityai/stable-diffusion-3.5-large) · [Community License](https://huggingface.co/stabilityai/stable-diffusion-3.5-large/blob/main/LICENSE.md)
- [FLUX.1 [dev] Non-Commercial License v2.0 — bfl.ai](https://bfl.ai/legal/non-commercial-license-terms)
- [stabilityai/stable-diffusion-xl-base-1.0 — HF (OpenRAIL++)](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
- [Efficient-Large-Model/Sana — HF (NSCL-NC, exclu)](https://huggingface.co/Efficient-Large-Model/Sana_1600M_1024px)

"""Vision de Klody — l'outil `analyser_image` fait « voir » une image au modèle.

Pont LÉGER vers le worker VL (Qwen2.5-VL…) servi par le gateway Klody Core
(:8090, routé par le champ `model`). Le CERVEAU reste TEXTE : la vision est un
outil À ARTEFACT — l'image est envoyée au worker VL, sa description revient en
texte dans la boucle ReAct. Le format des messages du cerveau ne change pas (pas
de `image_url` dans la conversation principale).

Robustesse façon tools/voice.py : toutes les erreurs reviennent en message
lisible pour le LLM — JAMAIS d'exception qui casserait le tour. VL_MODEL vide
(non configuré) → message « indisponible », l'outil reste enregistré.

Sécurité : whitelist d'extensions image. Interdit DE FAIT qu'un modèle se fasse
lire un secret (.env/.key/.pem) « comme une image » et l'exfiltre vers le worker
VL. Le chemin est validé contre les racines sandbox (mêmes que les autres outils).
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path

import config
from config import PROJECT_ROOT, build_allowed_roots, match_allowed_root

logger = logging.getLogger(__name__)

# Whitelist : seules ces extensions sont lues. Bloque par construction tout chemin
# vers un secret (un .env ne « passe » jamais pour une image).
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# Racines autorisées (projet courant + ALLOWED_ROOTS), comme tools/audio.py.
_VISION_ROOTS = build_allowed_roots(PROJECT_ROOT)

_DEFAULT_QUESTION = "Décris cette image en détail, en français."


def _resolve_image(path: str) -> Path:
    """Résout `path` (relatif au projet ou absolu) sous une racine autorisée.

    Lève PermissionError hors sandbox, ValueError si ce n'est pas une image /
    trop volumineuse / un répertoire, FileNotFoundError si le fichier n'existe pas.
    """
    if not path or not path.strip():
        raise ValueError("chemin vide")

    p = Path(path).expanduser()
    # resolve() déréférence TOUS les symlinks (y compris la cible finale) : on
    # valide donc le chemin réel. Un symlink qui sortirait des racines pointe vers
    # une cible hors sandbox → match_allowed_root la rejette ci-dessous.
    resolved = p.resolve() if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    if match_allowed_root(resolved, _VISION_ROOTS) is None:
        raise PermissionError(f"chemin hors des racines autorisées : {path}")

    if resolved.suffix.lower() not in _IMAGE_EXTS:
        raise ValueError(
            f"extension non-image : {resolved.suffix or '(aucune)'} "
            f"(acceptées : {', '.join(sorted(_IMAGE_EXTS))})"
        )
    if not resolved.exists():
        raise FileNotFoundError(f"image introuvable : {path}")
    if resolved.is_dir():
        raise ValueError(f"{path} est un répertoire, pas une image")

    size_mb = resolved.stat().st_size / (1024 * 1024)
    if size_mb > config.VL_MAX_IMAGE_MB:
        raise ValueError(
            f"image trop volumineuse : {size_mb:.1f} Mo (max {config.VL_MAX_IMAGE_MB:.0f} Mo)"
        )
    return resolved


def _data_uri(image: Path) -> str:
    """Encode l'image en data URI base64 (format attendu par `image_url`)."""
    mime = mimetypes.guess_type(image.name)[0] or "image/png"
    b64 = base64.b64encode(image.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def analyser_image(image_path: str, question: str = _DEFAULT_QUESTION) -> str:
    """Analyse une image avec le modèle vision local. Retourne un texte.

    Sert à LIRE ce que la boucle (texte) ne peut pas voir : capture d'écran,
    photo, schéma, diagramme, graphique, maquette UI, document scanné, OCR.
    Toutes les erreurs reviennent en message lisible — jamais d'exception.
    """
    if not config.VL_MODEL:
        return (
            "analyser_image indisponible : aucun modèle vision configuré. "
            "Pose VL_MODEL dans .env et démarre le worker VL (mlx-vlm via le gateway "
            "Klody Core)."
        )

    question = (question or _DEFAULT_QUESTION).strip() or _DEFAULT_QUESTION

    try:
        image = _resolve_image(image_path)
    except PermissionError as e:
        return f"analyser_image : accès refusé — {e}"
    except FileNotFoundError as e:
        return f"analyser_image : {e}"
    except ValueError as e:
        return f"analyser_image : {e}"

    try:
        data_uri = _data_uri(image)
    except OSError as e:
        return f"analyser_image : lecture du fichier impossible — {e}"

    # Client OpenAI DÉDIÉ au worker VL : on ne touche pas au client de la boucle
    # principale (un appel d'outil ne doit jamais détourner la conversation).
    from openai import OpenAI

    client = OpenAI(
        base_url=config.VL_BASE_URL,
        api_key=config.VL_API_KEY,
        timeout=config.LLM_HTTP_TIMEOUT,
        max_retries=0,
        default_headers={"X-Klody-App": "klody-ai"},  # journal d'usage gateway
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }
    ]
    try:
        resp = client.chat.completions.create(
            model=config.VL_MODEL,
            messages=messages,
            max_tokens=config.VL_MAX_TOKENS,
            temperature=0.2,
            stream=False,
        )
    except Exception as e:  # réseau / worker absent / modèle non chargé / 5xx
        # Détail complet dans les logs uniquement : ne PAS renvoyer l'exception
        # brute au LLM (peut contenir l'URL/clé/corps d'erreur du worker).
        logger.warning("analyser_image : échec de l'appel VL — %s", e)
        return (
            "analyser_image : le modèle vision n'a pas répondu (worker VL "
            "injoignable ou en erreur). Vérifie qu'il tourne (gateway :8090, "
            "mlx-vlm installé, modèle téléchargé)."
        )

    try:
        answer = (resp.choices[0].message.content or "").strip()
    except (AttributeError, IndexError):
        answer = ""
    if not answer:
        return "analyser_image : réponse vide du modèle vision."

    return f"🖼️ Analyse de {image.name} :\n\n{answer}"

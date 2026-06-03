"""Phase 1 du pipeline livre → skill : distille un livre en un JSON conforme à
`skills/distilled/schema.json`.

Le LLM est joint via le proxy RAG (:8081), qui :
  1. injecte du contexte LibraryBrain pertinent dans le system prompt,
  2. force le mode raisonnement (le proxy détecte le mot-clé "distille").

Usage :
    python -m scripts.distill_book \\
        --title "Le Pouvoir des habitudes" \\
        --author "Charles Duhigg" \\
        --year 2016 \\
        --domain productivity

Écrit dans `skills/distilled/<domain>/<slug>.json` (slug = kebab-case du titre).
Valide la sortie contre `skills/distilled/schema.json` avant écriture.

Sort en code 0 si tout est bon, 2 si le distillateur refuse (livre narratif ou
insuffisant), 1 si erreur technique.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import httpx
import jsonschema
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "prompts" / "distill-book.md"
SCHEMA_PATH = ROOT / "skills" / "distilled" / "schema.json"
DISTILLED_DIR = ROOT / "skills" / "distilled"

PROXY_URL = "http://127.0.0.1:8081/v1/chat/completions"
DEFAULT_TIMEOUT = 600.0   # le thinking + RAG + génération peuvent prendre du temps
DEFAULT_MAX_TOKENS = 8192
# Modèle MLX par défaut (cerveau). Override possible via --model si besoin.
DEFAULT_MODEL = "unsloth/Qwen3.6-35B-A3B-MLX-8bit"


def _slugify(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "skill"


def _build_prompt(template: str, *, title: str, author: str, year: int | None,
                  domain: str) -> str:
    """Substitue les variables Jinja-like `{{var}}` (string seulement)."""
    return (
        template
        .replace("{{book_title}}",   title)
        .replace("{{book_author}}",  author)
        .replace("{{book_year}}",    str(year) if year is not None else "null")
        .replace("{{target_domain}}", domain)
    )


def _extract_json(text: str) -> dict | None:
    """Extrait le premier objet JSON équilibré du texte.

    Tolère un bloc `<think>…</think>` en préambule (Qwen3 thinking) et un
    éventuel ``` ```json ``` ``` wrapper. Cherche `{` en début et scanne en
    suivant les accolades (en ignorant celles à l'intérieur des chaînes).
    """
    # Strip <think> blocks if mlx-lm collated them into content
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Drop a possible ```json ... ``` fence
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = text[start:i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return None
    return None


def _call_proxy(messages: list[dict], *, model: str, max_tokens: int,
                timeout: float) -> dict:
    """Appelle le proxy RAG, retourne le payload OpenAI complet."""
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    logger.info("POST {} (max_tokens={}, timeout={}s)", PROXY_URL, max_tokens, timeout)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(PROXY_URL, json=body)
        resp.raise_for_status()
        return resp.json()


def _repair(data: dict, schema: dict) -> dict:
    """Normalise des erreurs de forme fréquentes du modèle, sans inventer de
    contenu (les contenus manquants restent manquants → la validation jsonschema
    en aval signalera). Idempotent.

    - Élague les clés top-level inconnues du schéma (le modèle invente parfois
      `method`, `summary`, `notes`...).
    - `vocabulary` livré en liste de strings → liste d'objets
      `{term, definition: ""}`. L'item sera rejeté ensuite par le schéma si la
      définition reste vide (minLength=4), forçant l'auteur du prompt à corriger
      plutôt qu'à inventer ici.
    - `source.year` à `null` → clé retirée. Le modèle met `null` quand l'année
      est inconnue (cf. prompt), mais le schéma veut un entier *ou* l'absence de
      la clé (year est optionnel). Sans ça, tout livre sans millésime connu
      échoue à la validation alors que le reste du JSON est conforme.
    - `principles`/`checklist` : déduplique en gardant l'ordre.
    """
    allowed_top = set(schema.get("properties", {}).keys())
    extra = {k: data.pop(k) for k in list(data) if k not in allowed_top}
    if extra:
        logger.warning("clés hors-schéma élaguées : {}", sorted(extra))

    src = data.get("source")
    if isinstance(src, dict) and src.get("year") is None and "year" in src:
        src.pop("year")
        logger.warning("source.year=null retiré (clé optionnelle ; le schéma exige un entier)")

    vocab = data.get("vocabulary")
    if isinstance(vocab, list) and vocab and all(isinstance(v, str) for v in vocab):
        logger.warning("vocabulary livré en liste de strings — wrap en {{term, definition}}")
        data["vocabulary"] = [{"term": v, "definition": ""} for v in vocab]

    for key in ("principles", "checklist"):
        items = data.get(key)
        if isinstance(items, list):
            seen, dedup = set(), []
            for it in items:
                if isinstance(it, str) and it not in seen:
                    seen.add(it)
                    dedup.append(it)
            data[key] = dedup

    return data


def distill(*, title: str, author: str, year: int | None, domain: str,
            dry_run: bool = False, max_tokens: int = DEFAULT_MAX_TOKENS,
            timeout: float = DEFAULT_TIMEOUT, model: str = DEFAULT_MODEL) -> int:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _build_prompt(template, title=title, author=author, year=year, domain=domain)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    user_msg = (
        f"Distille la méthode du livre suivant :\n"
        f"- titre : {title}\n"
        f"- auteur : {author}\n"
        + (f"- année : {year}\n" if year is not None else "")
        + f"- domaine suggéré : {domain}\n"
        "Retourne uniquement le JSON conforme au schéma, rien autour."
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user",   "content": user_msg},
    ]
    payload = _call_proxy(messages, model=model, max_tokens=max_tokens, timeout=timeout)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("réponse proxy malformée : {}", exc)
        logger.debug("payload : {}", payload)
        return 1
    finish = payload["choices"][0].get("finish_reason")
    logger.info("réponse reçue ({} chars, finish_reason={})", len(content), finish)

    data = _extract_json(content)
    if data is None:
        logger.error("impossible d'extraire un JSON valide de la réponse")
        logger.error("contenu brut (200 premiers chars) : {}", content[:200])
        return 1

    # Cas refus prévu par le prompt
    if "error" in data and "skill" not in data:
        logger.warning("le distillateur a refusé : {}", data)
        return 2

    data = _repair(data, schema)

    # Validation schéma
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        logger.error("JSON non conforme au schéma : {}", exc.message)
        logger.error("chemin : {}", list(exc.absolute_path))
        # Dump pour inspection humaine
        debug_path = ROOT / "logs" / "distill_book_last_invalid.json"
        debug_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        logger.info("JSON invalide dumpé : {}", debug_path.relative_to(ROOT))
        return 1

    out_domain = data.get("domain", domain)
    out_slug = _slugify(data.get("skill", title))
    out_path = DISTILLED_DIR / out_domain / f"{out_slug}.json"
    if dry_run:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info("[dry-run] aurait écrit : {}", out_path)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    logger.info("✓ écrit : {}", out_path.relative_to(ROOT))
    print(str(out_path.relative_to(ROOT)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title",  required=True)
    parser.add_argument("--author", required=True)
    parser.add_argument("--year",   type=int, default=None)
    parser.add_argument("--domain", required=True,
                        help="ex: productivity, leadership, writing, negotiation")
    parser.add_argument("--dry-run", action="store_true",
                        help="affiche le JSON sans écrire le fichier")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--model",   default=DEFAULT_MODEL)
    args = parser.parse_args()

    return distill(
        title=args.title, author=args.author, year=args.year, domain=args.domain,
        dry_run=args.dry_run, max_tokens=args.max_tokens, timeout=args.timeout,
        model=args.model,
    )


if __name__ == "__main__":
    sys.exit(main())

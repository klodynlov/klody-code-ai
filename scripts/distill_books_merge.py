"""Phase 1bis — distille PLUSIEURS livres puis FUSIONNE en UN seul skill.

Pour chaque livre, on réutilise la distillation unitaire (en mémoire, sans
écrire de fichier intermédiaire) ; puis on demande au modèle (proxy RAG :8081)
de fusionner les N méthodes en UNE méthode actionnable conforme à
`skills/distilled/schema.json`.

Écrit `skills/distilled/<domain>/<slug>.json` et `print` son chemin relatif —
le wrapper `klody-distill.sh status` / l'outil `await_distillation` le
détectent alors comme « done ». Messages de log alignés sur distill_book.py
pour que le parsing de statut (refused / schema_invalid) marche tel quel.

Codes de sortie : 0 ok, 2 refus (aucun livre exploitable / fusion refusée),
1 erreur technique.

Usage :
    python scripts/distill_books_merge.py \\
        --skill "Maîtriser les algorithmes" --domain computing \\
        --book "Introduction to Algorithms|Cormen|2009" \\
        --book "The Algorithm Design Manual|Skiena|2020" \\
        --book "Algorithms|Sedgewick|2011"

Chaque --book vaut "titre|auteur|annee" (annee optionnelle : "-" ou vide).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import jsonschema
from loguru import logger

# Réutilise tout le socle de la distillation unitaire. Double import : marche en
# exécution directe (python scripts/distill_books_merge.py → scripts/ sur le path)
# ET en import packagé (tests : `from scripts.distill_books_merge import …`).
try:
    from distill_book import (  # type: ignore[import-not-found]
        DEFAULT_MAX_TOKENS,
        DEFAULT_MODEL,
        DEFAULT_TIMEOUT,
        DISTILLED_DIR,
        PROMPT_PATH,
        SCHEMA_PATH,
        _build_prompt,
        _call_proxy,
        _extract_json,
        _repair,
        _slugify,
    )
except ImportError:
    from scripts.distill_book import (  # type: ignore[no-redef]
        DEFAULT_MAX_TOKENS,
        DEFAULT_MODEL,
        DEFAULT_TIMEOUT,
        DISTILLED_DIR,
        PROMPT_PATH,
        SCHEMA_PATH,
        _build_prompt,
        _call_proxy,
        _extract_json,
        _repair,
        _slugify,
    )

ROOT = Path(__file__).resolve().parents[1]
MERGE_PROMPT_PATH = ROOT / "prompts" / "merge-skills.md"


def _parse_book(raw: str) -> tuple[str, str, int | None]:
    """"titre|auteur|annee" → (titre, auteur, annee|None)."""
    parts = [p.strip() for p in raw.split("|")]
    title = parts[0] if parts else ""
    author = parts[1] if len(parts) > 1 else ""
    year: int | None = None
    if len(parts) > 2 and parts[2] not in ("", "-"):
        try:
            year = int(parts[2])
        except ValueError:
            year = None
    return title, author, year


def distill_one(*, title: str, author: str, year: int | None, domain: str,
                model: str, timeout: float, max_tokens: int) -> dict | None:
    """Distille un livre et renvoie le JSON validé, ou None si refus/échec."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    prompt = _build_prompt(template, title=title, author=author, year=year, domain=domain)
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
        {"role": "user", "content": user_msg},
    ]
    logger.info("distillation : « {} » ({})", title, author)
    payload = _call_proxy(messages, model=model, max_tokens=max_tokens, timeout=timeout)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("réponse proxy malformée pour « {} » : {}", title, exc)
        return None

    data = _extract_json(content)
    if data is None:
        logger.warning("« {} » : JSON inextractible — livre ignoré du merge", title)
        return None
    if "error" in data and "skill" not in data:
        logger.warning("« {} » ignoré (le distillateur a refusé : {})", title, data.get("error"))
        return None

    data = _repair(data, schema)
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        logger.warning("« {} » : JSON non conforme ({}) — livre ignoré du merge", title, exc.message)
        return None
    return data


def _finalize_merged(data: dict, *, skill_name: str, domain: str) -> dict:
    """Normalise le JSON fusionné avant validation (idempotent, sans inventer).

    - `principles` : le schéma plafonne à 7 → troncature défensive.
    - `source` : le schéma ne tient qu'UN livre → on l'enlève (la provenance
      multiple vit dans la description).
    - force `skill` et `domain` aux valeurs demandées (le modèle dérive parfois).
    """
    pr = data.get("principles")
    if isinstance(pr, list) and len(pr) > 7:
        logger.warning("principles={} > 7 → tronqué à 7", len(pr))
        data["principles"] = pr[:7]
    data.pop("source", None)
    if skill_name:
        data["skill"] = skill_name
    if domain:
        data["domain"] = domain
    return data


def merge(distilled: list[dict], *, skill_name: str, domain: str,
          model: str, timeout: float, max_tokens: int) -> dict | None:
    """Fusionne N méthodes distillées en UNE, validée contre le schéma."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    template = MERGE_PROMPT_PATH.read_text(encoding="utf-8")
    system = (
        template
        .replace("{{skill_name}}", skill_name)
        .replace("{{target_domain}}", domain)
    )
    user_msg = (
        "Voici les méthodes distillées à fusionner en une seule "
        f"(skill « {skill_name} », domaine « {domain} ») :\n\n"
        + json.dumps(distilled, ensure_ascii=False, indent=2)
        + "\n\nRetourne uniquement le JSON fusionné conforme au schéma."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    logger.info("fusion de {} méthode(s) → skill « {} »", len(distilled), skill_name)
    payload = _call_proxy(messages, model=model, max_tokens=max_tokens, timeout=timeout)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("réponse proxy (merge) malformée : {}", exc)
        return None

    data = _extract_json(content)
    if data is None:
        logger.error("merge : impossible d'extraire un JSON valide de la réponse")
        return None
    if "error" in data and "skill" not in data:
        logger.warning("le distillateur a refusé : {}", data.get("error"))
        return None

    data = _repair(data, schema)
    data = _finalize_merged(data, skill_name=skill_name, domain=domain)
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        logger.error("JSON non conforme au schéma : {}", exc.message)
        debug_path = ROOT / "logs" / "distill_book_last_invalid.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("JSON invalide dumpé : {}", debug_path.relative_to(ROOT))
        return None
    return data


def run(*, skill_name: str, domain: str, books: list[str], model: str,
        timeout: float, max_tokens: int, dry_run: bool = False) -> int:
    distilled: list[dict] = []
    for raw in books:
        title, author, year = _parse_book(raw)
        if not title or not author:
            logger.warning("livre ignoré (titre/auteur manquant) : « {} »", raw)
            continue
        data = distill_one(title=title, author=author, year=year, domain=domain,
                           model=model, timeout=timeout, max_tokens=max_tokens)
        if data is not None:
            distilled.append(data)

    if not distilled:
        logger.warning("le distillateur a refusé : aucun livre exploitable parmi {}", len(books))
        return 2

    merged = merge(distilled, skill_name=skill_name, domain=domain,
                   model=model, timeout=timeout, max_tokens=max_tokens)
    if merged is None:
        return 1

    out_domain = merged.get("domain", domain)
    out_slug = _slugify(merged.get("skill", skill_name))
    out_path = DISTILLED_DIR / out_domain / f"{out_slug}.json"
    if dry_run:
        print(json.dumps(merged, ensure_ascii=False, indent=2))
        logger.info("[dry-run] aurait écrit : {}", out_path)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("✓ écrit : {} (fusion de {} livre(s))", out_path.relative_to(ROOT), len(distilled))
    print(str(out_path.relative_to(ROOT)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", required=True, help="Nom du skill fusionné")
    parser.add_argument("--domain", required=True,
                        help="ex: computing, productivity, leadership")
    parser.add_argument("--book", action="append", default=[], dest="books",
                        help='"titre|auteur|annee" (répétable, ≥1)')
    parser.add_argument("--dry-run", action="store_true",
                        help="affiche le JSON fusionné sans écrire le fichier")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if not args.books:
        parser.error("au moins un --book est requis")

    return run(
        skill_name=args.skill, domain=args.domain, books=args.books,
        model=args.model, timeout=args.timeout, max_tokens=args.max_tokens,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())

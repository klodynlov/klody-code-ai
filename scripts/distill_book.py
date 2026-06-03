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
import os
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
# LibraryBrain (métadonnées livres) — même variable/défaut que config.LIBRARYBRAIN_URL.
LIBRARYBRAIN_URL = os.getenv("LIBRARYBRAIN_URL", "http://127.0.0.1:8765/api/ask")
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


def _norm_title(text: str) -> str:
    """Titre normalisé pour comparaison : minuscule, ASCII, ponctuation → espaces."""
    ascii_text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


# Mots vides ignorés pour comparer des titres (EN + FR).
_TITLE_STOPWORDS = frozenset({
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with",
    "de", "des", "du", "la", "le", "les", "un", "une", "et",
})


def _title_words(text: str) -> set[str]:
    """Mots significatifs d'un titre (normalisés, sans mots vides ni mono-lettres)."""
    return {w for w in _norm_title(text).split() if len(w) > 1 and w not in _TITLE_STOPWORDS}


def _title_overlap(requested: str, candidate: str) -> float:
    """Coefficient de recouvrement (Szymkiewicz–Simpson) sur les mots significatifs :
    |∩| / min(|req|, |cand|). Robuste aux sous-titres : si un titre est sous-ensemble
    de l'autre → 1.0 (ex. « Method Validation… » vs « Method Validation… - A Guide »).
    Renvoie 0.0 si l'un des deux n'a aucun mot significatif.
    """
    req = _title_words(requested)
    cand = _title_words(candidate)
    if not req or not cand:
        return 0.0
    return len(req & cand) / min(len(req), len(cand))


def _pick_source_from_sources(title: str, sources: list[dict]) -> dict | None:
    """Parmi les sources LibraryBrain ({title, author, …}), choisit le livre dont
    le titre recouvre le mieux `title` et renvoie {book, author} ancrés.

    Conservateur : exige un recouvrement ≥ 0.6, au moins 2 mots significatifs en
    commun, ET un auteur non vide — sinon None (le livre trouvé n'est probablement
    pas celui demandé → on ne réécrit pas la source à l'aveugle).
    """
    req_words = _title_words(title)
    best: dict | None = None
    best_overlap = 0.0
    for s in sources:
        cand_title = (s.get("title") or "").strip()
        author = (s.get("author") or "").strip()
        if not cand_title or not author:
            continue
        cand_words = _title_words(cand_title)
        if not req_words or not cand_words:
            continue
        shared = len(req_words & cand_words)
        if shared < 2:
            continue
        overlap = shared / min(len(req_words), len(cand_words))
        if overlap > best_overlap:
            best_overlap = overlap
            best = {"book": cand_title, "author": author}
    return best if best is not None and best_overlap >= 0.6 else None


def _resolve_source(title: str, *, timeout: float = 8.0) -> dict | None:
    """Interroge LibraryBrain pour ancrer {book, author} sur des métadonnées
    réelles plutôt que sur ce que le modèle a inventé (il hallucine souvent
    l'auteur quand l'utilisateur n'en fournit pas).

    Renvoie None — et on garde la valeur du modèle — si LibraryBrain est
    injoignable, ne trouve rien, ou ne matche pas franchement le titre.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(LIBRARYBRAIN_URL, json={"query": title, "limit": 5})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # injoignable, HTTP error, JSON invalide…
        logger.warning("LibraryBrain injoignable pour l'ancrage auteur ({}) — valeur du modèle conservée", exc)
        return None
    if not data.get("found"):
        logger.info("LibraryBrain n'a pas trouvé « {} » — auteur du modèle conservé", title)
        return None
    return _pick_source_from_sources(title, data.get("sources", []))


def _apply_grounded_source(data: dict, grounded: dict | None) -> dict:
    """Réécrit source.book/author avec les valeurs ancrées (LibraryBrain).

    No-op si `grounded` est None. Préserve source.year (LibraryBrain ne l'expose
    pas). Loggue le remplacement quand l'auteur du modèle différait — c'est là
    qu'on attrape l'hallucination.
    """
    if not grounded:
        return data
    src = data.setdefault("source", {})
    old_author = src.get("author")
    if old_author and old_author != grounded["author"]:
        logger.warning("auteur ancré sur LibraryBrain : « {} » → « {} »", old_author, grounded["author"])
    src["book"] = grounded["book"]
    src["author"] = grounded["author"]
    return data


def distill(*, title: str, author: str, year: int | None, domain: str,
            dry_run: bool = False, max_tokens: int = DEFAULT_MAX_TOKENS,
            timeout: float = DEFAULT_TIMEOUT, model: str = DEFAULT_MODEL,
            ground_source: bool = True) -> int:
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

    # Ancrage de la source sur LibraryBrain : remplace l'auteur (souvent halluciné
    # par le modèle, surtout quand l'utilisateur n'a pas fourni de --author fiable)
    # par la métadonnée réelle du livre. Sans effet si désactivé, ou si LibraryBrain
    # est injoignable / ne confirme pas le titre (on garde alors la valeur du modèle).
    if ground_source:
        data = _apply_grounded_source(data, _resolve_source(title))

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
    parser.add_argument("--no-ground-source", action="store_false", dest="ground_source",
                        help="ne pas ancrer source.author sur LibraryBrain (garde --author tel quel)")
    args = parser.parse_args()

    return distill(
        title=args.title, author=args.author, year=args.year, domain=args.domain,
        dry_run=args.dry_run, max_tokens=args.max_tokens, timeout=args.timeout,
        model=args.model, ground_source=args.ground_source,
    )


if __name__ == "__main__":
    sys.exit(main())

"""
Système de compétences persistantes de Klody.
Klody peut sauvegarder des patterns, snippets ou connaissances utiles
qui seront rechargés automatiquement à chaque démarrage.
"""

import json
import logging
import re
import unicodedata
from datetime import datetime

from config import SKILLS_DIR

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Transforme un nom en slug sûr pour un nom de fichier : ascii, minuscules,
    accents translittérés (é→e), tout caractère non [a-z0-9-] remplacé par '_'."""
    ascii_name = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9-]+", "_", ascii_name).strip("_-")
    return slug[:40].strip("_-") or "skill"


def save_skill(name: str, description: str, content: str) -> str:
    """Sauvegarde une compétence ou un pattern utile."""
    SKILLS_DIR.mkdir(exist_ok=True)

    slug = _slugify(name)
    path = SKILLS_DIR / f"{slug}.json"

    existed = path.exists()
    data = {
        "name": name,
        "slug": slug,
        "description": description,
        "content": content,
        "updated": datetime.now().isoformat(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    action = "mise à jour" if existed else "sauvegardée"
    logger.info("Compétence %s: %s", action, slug)
    return f"Compétence « {name} » {action} ({path.name})"


def load_skills() -> list[dict]:
    """Charge toutes les compétences sauvegardées."""
    if not SKILLS_DIR.exists():
        return []
    skills = []
    for f in sorted(SKILLS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            if isinstance(data, dict):  # ignorer les fichiers de domaine (tableaux)
                skills.append(data)
        except Exception:
            continue
    return skills


def list_skills() -> str:
    """Retourne un résumé textuel des compétences sauvegardées."""
    skills = load_skills()
    if not skills:
        return "Aucune compétence sauvegardée."
    lines = [f"**{len(skills)} compétence(s) :**\n"]
    for s in skills:
        updated = s.get("updated", "")[:10]
        lines.append(f"- **{s['name']}** (`{s['slug']}`) — {s['description']}  [mis à jour: {updated}]")
    return "\n".join(lines)


def delete_skill(slug: str) -> str:
    """Supprime une compétence utilisateur par son slug."""
    path = SKILLS_DIR / f"{slug}.json"
    if not path.exists():
        available = [f.stem for f in sorted(SKILLS_DIR.glob("*.json"))
                     if _is_user_skill(f)]
        hint = f" Disponibles : {', '.join(available)}" if available else ""
        return f"Skill '{slug}' introuvable.{hint}"

    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return f"ERREUR: '{slug}' est un fichier de domaine (lecture seule)."
        name = data.get("name", slug)
    except Exception:
        name = slug

    path.unlink()
    logger.info("Skill supprimé: %s", slug)
    return f"Compétence « {name} » supprimée."


def _is_user_skill(path) -> bool:
    """Retourne True si le fichier est un user skill (dict), pas un domaine (liste)."""
    try:
        return isinstance(json.loads(path.read_text()), dict)
    except Exception:
        return False


# Skills toujours injectés (contexte utilisateur + règles), jamais filtrés.
_ALWAYS_PREFIXES = ("utilisateur_", "conventions_")
_STOP = {
    "les", "des", "une", "pour", "avec", "dans", "que", "qui", "sur", "par",
    "est", "son", "ses", "mon", "ton", "the", "and", "for", "with", "you",
    "mais", "plus", "tout", "tous", "fait", "faire", "comment", "peux", "moi",
    "this", "that", "veux", "vais", "puis", "quoi", "donne",
    "klody", "skill", "skills", "fichier", "fichiers", "code",  # trop fréquents → non discriminants
}


def _skill_terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zà-ÿ0-9]{3,}", (text or "").lower()) if t not in _STOP}


def _score_skill(terms: set[str], skill: dict) -> int:
    """Score de pertinence d'un skill pour des termes de requête.

    Tolère les variantes de tokenisation (`nextjs` ↔ `next_js`/`Next.js`) via
    un matching sous-chaîne bidirectionnel (≥4 car.) en plus de l'inclusion
    directe dans le texte du skill (nom + description + slug)."""
    hay_str = f"{skill.get('name', '')} {skill.get('description', '')} {skill.get('slug', '')}".lower()
    hay_terms = _skill_terms(hay_str)
    score = 0
    for t in terms:
        if t in hay_str or any((len(h) >= 4 and h in t) or (len(t) >= 4 and t in h) for h in hay_terms):
            score += 1
    return score


def select_skills(skills: list[dict], query: str = "", k: int = 5) -> list[dict]:
    """Sélectionne les skills à injecter dans le prompt.

    - Skills de profil/règles (slug `utilisateur_*` / `conventions_*`) : toujours
      inclus (contexte permanent sur l'utilisateur).
    - Skills « how-to » (mlx, nextjs, distiller_un_livre…) : filtrés par
      pertinence au prompt (overlap de mots-clés sur nom + description + slug),
      seuls les `k` meilleurs avec score > 0 sont injectés. Query vide → aucun.

    Évite d'injecter ~6k tokens de skills à chaque message quand un seul est
    pertinent. Filtrage par mots-clés : déterministe, hors-ligne, sans embeddings.
    """
    always, howto = [], []
    for s in skills:
        (always if str(s.get("slug", "")).startswith(_ALWAYS_PREFIXES) else howto).append(s)
    terms = _skill_terms(query)
    if not terms:
        return always
    ranked = sorted(((_score_skill(terms, s), s) for s in howto), key=lambda x: -x[0])
    picked = [s for score, s in ranked if score > 0][:k]
    return always + picked


def format_skills_for_prompt(skills: list[dict]) -> str:
    """Formate les compétences pour injection dans le system prompt."""
    if not skills:
        return ""
    lines = ["\n\n## Compétences acquises"]
    for s in skills:
        lines.append(f"\n### {s['name']}")
        lines.append(f"{s['description']}")
        lines.append(f"```\n{s['content']}\n```")
    return "\n".join(lines)

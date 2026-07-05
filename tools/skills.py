"""
Système de compétences persistantes de Klody.
Klody peut sauvegarder des patterns, snippets ou connaissances utiles
qui seront rechargés automatiquement à chaque démarrage.
"""

import json
import logging
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime

from config import SKILLS_DIR

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Transforme un nom en slug sûr pour un nom de fichier : ascii, minuscules,
    accents translittérés (é→e), tout caractère non [a-z0-9-] remplacé par '_'."""
    ascii_name = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9-]+", "_", ascii_name).strip("_-")
    return slug[:40].strip("_-") or "skill"


# Barrière anti-traversée pour delete_skill : un slug fourni par le modèle
# (tool `delete_skill`) ou par l'URL (DELETE /api/skills/{slug}) ne doit contenir
# QUE [a-z0-9_-] — exactement ce que `_slugify` produit. Exclut '/', '..' et les
# chemins absolus. Barrière reconnue par CodeQL (py/path-injection).
_SKILL_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def save_skill(name: str, description: str, content: str, code_compatible: bool = False) -> str:
    """Sauvegarde une compétence ou un pattern utile.

    `code_compatible=True` marque le skill comme utilisable sur une tâche de code
    (il pourra alors être injecté — compact — au modèle coder si
    config.SKILLS_ON_CODER_ENABLED). Par défaut False : le skill ne touche que le
    brain. Cf. _skill_is_code_compatible / format_skills_compact."""
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
    if code_compatible:
        data["code_compatible"] = True
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
    if not _SKILL_SLUG_RE.fullmatch(slug or ""):
        # Slug non maîtrisé (traversée / caractères hors allowlist) → refus net,
        # même message que « introuvable » pour ne rien divulguer.
        return f"Skill '{slug}' introuvable."
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
    "outil", "outils", "tool", "tools",  # « code-moi un outil » n'est pas un sujet → évite le faux positif distiller_*
}


def _skill_terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zà-ÿ0-9]{3,}", (text or "").lower()) if t not in _STOP}


def _term_matches(t: str, hay_str: str, hay_terms: set[str]) -> bool:
    """Un terme de requête « touche » un skill : inclusion directe dans le texte
    (nom+desc+slug), ou sous-chaîne bidirectionnelle ≥4 car. (gère `nextjs` ↔
    `next_js`/`Next.js`)."""
    return t in hay_str or any(
        (len(h) >= 4 and h in t) or (len(t) >= 4 and t in h) for h in hay_terms
    )


def _matching_terms(terms: set[str], skill: dict) -> set[str]:
    """Sous-ensemble des termes de la requête présents dans le skill."""
    hay_str = f"{skill.get('name', '')} {skill.get('description', '')} {skill.get('slug', '')}".lower()
    hay_terms = _skill_terms(hay_str)
    return {t for t in terms if _term_matches(t, hay_str, hay_terms)}


def _score_skill(terms: set[str], skill: dict) -> int:
    """Overlap brut : nombre de termes de la requête présents dans le skill."""
    return len(_matching_terms(terms, skill))


def select_skills(skills: list[dict], query: str = "", k: int = 5) -> list[dict]:
    """Sélectionne les skills à injecter dans le prompt.

    - Skills de profil/règles (slug `utilisateur_*` / `conventions_*`) : toujours
      inclus (contexte permanent sur l'utilisateur).
    - Skills « how-to » (mlx, nextjs, distiller_un_livre…) : filtrés par
      pertinence au prompt, pondérés IDF, seuls les `k` meilleurs sont injectés.
      Query vide → aucun.

    Pondération IDF (déterministe, hors-ligne, sans embeddings) : un terme rare
    dans le corpus de skills (`dijkstra`, `nextjs`) pèse fort, un terme partagé
    par plusieurs skills (`fusion`) pèse faible. Garde-fou homonyme : un skill
    dont le SEUL terme commun est partagé (df > 1) est rejeté — c'est ce qui
    évite que « tri fusion » pêche le skill de distillation (« fusionner des
    livres »). Évite aussi d'injecter ~6k tokens de skills quand un seul compte.
    """
    always, howto = [], []
    for s in skills:
        (always if str(s.get("slug", "")).startswith(_ALWAYS_PREFIXES) else howto).append(s)
    terms = _skill_terms(query)
    if not terms:
        return always

    matched = [(s, _matching_terms(terms, s)) for s in howto]
    df: Counter = Counter()
    for _, mt in matched:
        df.update(mt)
    n = len(howto)

    def _idf(t: str) -> float:
        # df ∈ [1, n] ⇒ poids ∈ (0, log(n+1)] ; terme présent dans tous → ~0.
        return math.log((1 + n) / df[t])

    scored: list[tuple[float, dict]] = []
    for s, mt in matched:
        if not mt:
            continue
        # Signal trop faible : un unique terme commun, et partagé par d'autres skills.
        if len(mt) == 1 and df[next(iter(mt))] > 1:
            continue
        scored.append((sum(_idf(t) for t in mt), s))

    scored.sort(key=lambda x: -x[0])
    picked = [s for _, s in scored[:k]]
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


def _skill_is_code_compatible(skill: dict) -> bool:
    """Le skill est-il explicitement marqué utilisable sur une tâche de code ?

    Drapeau booléen `code_compatible: true` (modèle calqué sur `interactive`).
    Par défaut False → aucun skill n'atteint le modèle coder tant qu'il n'est pas
    taggé, ce qui préserve le comportement slim historique du coder."""
    return skill.get("code_compatible") is True


def format_skills_compact(skills: list[dict], max_chars: int = 800) -> str:
    """Rendu MINIMAL des skills pour le modèle coder (complétion, fragile).

    Contrairement à format_skills_for_prompt (qui dump le `content` intégral dans
    un bloc ```), on n'émet qu'un titre + la description + un `content` TRONQUÉ à
    `max_chars`. But : donner la piste utile sans réveiller la dégénérescence du
    coder sous un gros prompt."""
    if not skills:
        return ""
    max_chars = max(1, max_chars)  # plancher : évite une troncature à 0 → ' […]' isolé
    lines = ["\n\n## Compétence(s) pertinente(s)"]
    for s in skills:
        lines.append(f"\n### {s.get('name', s.get('slug', ''))}")
        desc = (s.get("description") or "").strip()
        if desc:
            lines.append(desc)
        content = (s.get("content") or "").strip()
        if content:
            if len(content) > max_chars:
                content = content[:max_chars].rstrip() + " […]"
            lines.append(content)
    return "\n".join(lines)

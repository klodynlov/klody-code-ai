"""
Système de compétences persistantes de Klody.
Klody peut sauvegarder des patterns, snippets ou connaissances utiles
qui seront rechargés automatiquement à chaque démarrage.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from config import SKILLS_DIR

logger = logging.getLogger(__name__)


def save_skill(name: str, description: str, content: str) -> str:
    """Sauvegarde une compétence ou un pattern utile."""
    SKILLS_DIR.mkdir(exist_ok=True)

    slug = name.lower().replace(" ", "_").replace("/", "_")[:40]
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

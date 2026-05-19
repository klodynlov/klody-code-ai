"""
Système de compétences persistantes de Klody.
Klody peut sauvegarder des patterns, snippets ou connaissances utiles
qui seront rechargés automatiquement à chaque démarrage.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"
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
            skills.append(json.loads(f.read_text()))
        except Exception:
            continue
    return skills


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

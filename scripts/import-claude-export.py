"""
Import d'un export Claude.ai vers les skills KlodyAI.

Usage :
    python scripts/import-claude-export.py <dossier-export>

Crée des fichiers skill JSON dans skills/ à partir de :
  - memories.json   → profil utilisateur détaillé (contexte, musique, tech, préférences)
  - projects/*.json → descriptions des projets actifs
  - conversations.json → statistiques et stack technique détectée

Les skills créés sont chargés automatiquement au prochain démarrage de KlodyAI.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Ajouter la racine du projet au path pour importer config
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SKILLS_DIR  # noqa: E402

EXPORT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None

# Technologies à détecter dans les conversations
TECH_PATTERNS = [
    "next.js", "nextjs", "react", "symfony", "php", "python", "fastapi",
    "tailwind", "typescript", "javascript", "node", "docker", "nginx", "pm2",
    "mlx", "ollama", "lora", "sqlite", "postgresql", "redis", "prisma",
    "logic pro", "mpc", "apollo", "uad", "ableton", "reaper",
    "vps", "ssh", "vercel", "mailchimp", "distrokid", "sacem",
]


# ── Helpers ─────────────────────────────────────────────────────────────────

def save_skill(slug: str, name: str, description: str, content: str) -> Path:
    path = SKILLS_DIR / f"{slug}.json"
    path.write_text(json.dumps({
        "name": name,
        "slug": slug,
        "description": description,
        "content": content,
        "updated": datetime.now().isoformat(),
        "source": "claude-export",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def section(memories: str, heading: str) -> str:
    """Extrait un bloc délimité par un heading markdown de niveau égal (** … **)."""
    # Stop à la prochaine ligne commençant par --- ou par ** (hors indentation)
    pattern = rf"\*\*{re.escape(heading)}\*\*\n+(.*?)(?=\n---|\n\*\*[A-Z]|\Z)"
    m = re.search(pattern, memories, re.DOTALL)
    return m.group(1).strip() if m else ""


def between_markers(text: str, start: str, end: str) -> str:
    """Extrait le texte entre deux marqueurs italiques (* … *)."""
    pattern = rf"\*{re.escape(start)}\*\n+(.*?)(?=\n\*{re.escape(end)}\*|\Z)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


# ── 1. memories.json ────────────────────────────────────────────────────────

def import_memories(export_dir: Path) -> list[Path]:
    path = export_dir / "memories.json"
    if not path.exists():
        print("  ⚠  memories.json absent")
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data[0]["conversations_memory"] if isinstance(data, list) else data.get("conversations_memory", "")
    if not raw:
        return []

    created: list[Path] = []

    # — Profil utilisateur : contexte pro + perso
    work = section(raw, "Work context")
    personal = section(raw, "Personal context")
    if work or personal:
        content = ""
        if work:
            content += f"## Contexte professionnel\n{work}\n\n"
        if personal:
            content += f"## Contexte personnel\n{personal}"
        p = save_skill(
            slug="utilisateur_profil",
            name="Profil utilisateur — Klod / Klody",
            description="Qui est l'utilisateur : rôle pro (Mondial Relay), artiste (Klod Ynlov), origines, valeurs, style de travail.",
            content=content.strip(),
        )
        created.append(p)
        print(f"  ✓ Profil utilisateur ({len(content)} chars)")

    # — Musique et studio (section "Recent months" dans "Brief history")
    studio = between_markers(raw, "Recent months", "Earlier context")
    if studio:
        p = save_skill(
            slug="utilisateur_studio",
            name="Setup studio & workflow musical",
            description="Matériel, chaîne UAD, workflow MPC × Logic Pro, micro AT4047/SV, objectifs loudness, compétences vocales.",
            content=studio,
        )
        created.append(p)
        print(f"  ✓ Setup studio ({len(studio)} chars)")

    # — Stack tech + projets passés (section "Earlier context")
    earlier = between_markers(raw, "Earlier context", "Long-term background")
    top_mind = section(raw, "Top of mind")
    tech_content = ""
    if top_mind:
        tech_content += f"## Priorités actuelles\n{top_mind}\n\n"
    if earlier:
        tech_content += f"## Projets et acquis récents\n{earlier}"
    if tech_content:
        p = save_skill(
            slug="utilisateur_tech_contexte",
            name="Contexte tech & projets actifs",
            description="Stack maîtrisé, projets en cours (karaibart.fr, MCP Logic Pro, LibraryBrain, KlodyAI), MacBook M5 Max 128 GB.",
            content=tech_content.strip(),
        )
        created.append(p)
        print(f"  ✓ Contexte tech ({len(tech_content)} chars)")

    # — Préférences d'interaction
    prefs = section(raw, "Interaction preferences (always apply)")
    longterm = between_markers(raw, "Long-term background", "__NONE__")
    pref_content = ""
    if longterm:
        pref_content += f"## Méthode de travail établie\n{longterm}\n\n"
    if prefs:
        pref_content += f"## Préférences d'interaction\n{prefs}"
    if pref_content:
        p = save_skill(
            slug="utilisateur_preferences",
            name="Préférences & méthode de travail",
            description="Style de réponse attendu, ton, niveau de détail, règle d'itération, zéro flatterie.",
            content=pref_content.strip(),
        )
        created.append(p)
        print(f"  ✓ Préférences d'interaction ({len(pref_content)} chars)")

    return created


# ── 2. projects/*.json ───────────────────────────────────────────────────────

def import_projects(export_dir: Path) -> list[Path]:
    proj_dir = export_dir / "projects"
    if not proj_dir.exists():
        return []

    projects: list[dict] = []
    for f in sorted(proj_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name = data.get("name", "").strip()
            desc = data.get("description", "").strip()
            if name:
                projects.append({"name": name, "description": desc})
        except Exception:
            continue

    if not projects:
        return []

    lines = ["## Projets Claude actifs\n"]
    for p in projects:
        lines.append(f"### {p['name']}")
        if p["description"]:
            lines.append(p["description"])
        lines.append("")

    content = "\n".join(lines).strip()
    path = save_skill(
        slug="utilisateur_projets",
        name="Projets actifs (export Claude)",
        description="Liste des projets Claude de l'utilisateur : SampleBrain, LibraryBrain, KlodyAI, VocalBrain, karaibart, etc.",
        content=content,
    )
    print(f"  ✓ {len(projects)} projets importés")
    return [path]


# ── 3. conversations.json — stats & stack ───────────────────────────────────

def import_conversations(export_dir: Path) -> list[Path]:
    path = export_dir / "conversations.json"
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠  conversations.json illisible: {e}")
        return []

    if not isinstance(data, list):
        return []

    tech_counter: Counter = Counter()
    topic_lines: list[str] = []

    for conv in data:
        title = conv.get("name", conv.get("title", "")).strip()
        summary = conv.get("summary", "")
        blob = (title + " " + summary).lower()

        for tech in TECH_PATTERNS:
            if tech in blob:
                tech_counter[tech] += 1

        if title:
            topic_lines.append(f"- {title}")

    top_tech = [t for t, _ in tech_counter.most_common(20)]
    topics_sample = topic_lines[:40]

    content = f"""## Statistiques
- {len(data)} conversations analysées
- Technologies mentionnées : {', '.join(top_tech) or 'aucune détectée'}

## Sujets fréquents (40 premiers titres)
{chr(10).join(topics_sample)}
"""
    result_path = save_skill(
        slug="utilisateur_conversations_stats",
        name="Statistiques conversations Claude",
        description=f"{len(data)} conversations analysées — stack tech détecté, sujets fréquents.",
        content=content.strip(),
    )
    print(f"  ✓ {len(data)} conversations analysées — top tech: {', '.join(top_tech[:8])}")
    return [result_path]


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not EXPORT_DIR or not EXPORT_DIR.exists():
        print(f"Usage: python scripts/import-claude-export.py <dossier-export>")
        print(f"Dossier non trouvé: {EXPORT_DIR}")
        sys.exit(1)

    SKILLS_DIR.mkdir(exist_ok=True)
    print(f"\nImport depuis : {EXPORT_DIR.name}")
    print(f"Destination   : {SKILLS_DIR}\n")

    created: list[Path] = []
    created += import_memories(EXPORT_DIR)
    created += import_projects(EXPORT_DIR)
    created += import_conversations(EXPORT_DIR)

    print(f"\n{'─' * 50}")
    print(f"  {len(created)} fichier(s) skill créé(s) dans skills/")
    for p in created:
        print(f"    → {p.name}")
    print(f"\nKlodyAI chargera ces skills au prochain démarrage.")
    print(f"Pour vérifier : python main.py → /memory\n")


if __name__ == "__main__":
    main()

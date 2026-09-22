"""Import curated LibraryBrain skills into Klody's live skill format.

Usage: .venv/bin/python scripts/import-librarybrain-methods.py /path/LibraryBrainSkills [--domain blender]
Original Markdown and evidence are retained beside the portable JSON exports.
An existing unrelated skill is never overwritten.
"""
import argparse
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = str(Path.home())


def _sanitize_home(snapshot):
    """Remplace le home absolu par « ~ » dans les fichiers texte copiés.

    Les bundles LibraryBrain embarquent les chemins de la bibliothèque
    personnelle (`provenance.json` → `file_path`, `database`). Ce dépôt est
    public : publier ces chemins divulguerait l'arborescence de la machine
    sans rien apporter — les titres et auteurs restent dans `sources.md`.
    """
    for path in snapshot.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".md", ".txt", ".yaml", ".toml"}:
            continue
        text = path.read_text()
        if HOME in text:
            path.write_text(text.replace(HOME, "~"))
DOMAINS = {
    "unity": ("Unity — prototype interactif et pipeline Blender", "Aide Unity 6, Unity 6.6 et URP : créer un prototype interactif ou un jeu 3D, importer un asset Blender FBX, corriger échelle, axes, UV et matériaux roses. Prefabs, animation, interactions C#, Input System, performances, Profiler, build macOS et validation dans le player."),
    "research": ("Recherche et distillation des sources", "Comparer des sources, arbitrer des recommandations divergentes, extraire des méthodes traçables d'un corpus et tester leurs limites."),
    "music": ("Composition et mixage", "Améliorer un mix, mixer une chanson, rééquilibrer le mixage et les rôles des instruments, dégager une voix masquée ou un arrangement encombré. Comparer des compresseurs et une compression à volume perçu égal ; retravailler structure et transitions."),
    "business": ("Offre, prix et acquisition", "Construire une offre rentable, estimer sa valeur, fixer un prix, calculer marge, contribution, seuil de rentabilité, capacité et choisir un test marketing d'acquisition."),
    "engineering": ("Ingénierie LLM et évaluation", "Choisir instructions, RAG ou fine-tuning pour un LLM. Diagnostiquer retrieval et génération, mesurer les réponses et construire une évaluation sans fuite de données."),
    "blender": ("Blender — création 3D et méthodes", "Aide pour débuter, apprendre et travailler dans Blender. Créer une scène 3D, modéliser un objet, corriger topologie, sculpture, UV et textures étirées. Matériaux PBR, shader, baking de normal maps, Geometry Nodes, paysages, forêt et dispersion. Améliorer éclairage et rendu Cycles/EEVEE, réduire le bruit, animer un personnage, rigging et simulations. Exporter Blender vers Unity, Three.js, GLB/glTF ou impression 3D."),
}


def run(source, domains=None):
    stamp = datetime.now(UTC).isoformat()
    destination = ROOT / "integrations/librarybrain"
    skills_dir = ROOT / "skills"
    exports = []
    prepared = []
    for domain in (domains if domains is not None else DOMAINS):
        name, description = DOMAINS[domain]
        slug = f"librarybrain_{domain}"
        original = source / "skills" / f"librarybrain-{domain}"
        raw = (original / "SKILL.md").read_text()
        assert raw.startswith("---\n"), original
        body = raw.split("---\n", 2)[2].strip()
        json.loads((original / "references/provenance.json").read_text())
        provenance = destination / f"librarybrain-{domain}/references/provenance.json"
        snapshot = destination / original.name
        # Les chemins ÉCRITS dans le JSON sont relatifs à la racine du dépôt :
        # ce fichier est versionné dans un dépôt public, un chemin absolu y
        # divulguerait le nom de session et l'arborescence de la machine.
        # Les opérations de fichiers ci-dessous restent absolues.
        rel_snapshot = snapshot.relative_to(ROOT)
        body = re.sub(
            r"\]\(((?:references|scripts)/[^)#]+)(#[^)]*)?\)",
            lambda match, base=rel_snapshot: f"]({base / match[1]}{match[2] or ''})",
            body,
        )
        resources = sorted(path for path in original.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
        bundle_hash = hashlib.sha256()
        for resource in resources:
            bundle_hash.update(str(resource.relative_to(original)).encode() + b"\0")
            bundle_hash.update(resource.read_bytes())
            bundle_hash.update(b"\0")
        data = {
            "name": f"LibraryBrain — {name}", "slug": slug,
            "description": description, "content": body,
            "updated": stamp, "code_compatible": domain == "engineering",
            "source": "LibraryBrainSkills/curated-methods-v1",
            "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "bundle_sha256": bundle_hash.hexdigest(),
            "provenance": str(provenance.relative_to(ROOT)),
        }
        target = skills_dir / f"{slug}.json"
        if target.exists() and json.loads(target.read_text()).get("source") != data["source"]:
            raise RuntimeError(f"Unrelated existing skill: {target}")
        prepared.append((original, data, target))
    for original, data, target in prepared:
        snapshot = destination / original.name
        shutil.copytree(original, snapshot, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        _sanitize_home(snapshot)
        skills_dir.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        (snapshot / "klody-skill.json").write_text(rendered)
        target.write_text(rendered)
        exports.append({"slug": data["slug"], "path": str(target.relative_to(ROOT)), "source_sha256": data["source_sha256"], "bundle_sha256": data["bundle_sha256"]})
    report_path = destination / "import-report.json"
    previous = json.loads(report_path.read_text()).get("skills", []) if report_path.exists() else []
    combined = {entry["slug"]: entry for entry in previous}
    combined.update({entry["slug"]: entry for entry in exports})
    report_path.write_text(json.dumps({"date": stamp, "skills": list(combined.values())}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(exports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--domain", action="append", choices=DOMAINS, dest="domains")
    args = parser.parse_args()
    run(args.source.resolve(), args.domains)

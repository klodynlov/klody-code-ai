"""Select capabilities without putting the entire tool registry in each prompt.

This is relevance routing, not an authorization boundary. All native handlers
remain available; find_tools can add a capability during the same agent run.
Only user messages determine the initial selection, never retrieved text.
"""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def matches(text: str, aliases: tuple[str, ...]) -> bool:
    return any(re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text)
               for alias in aliases)


# Explicit application names are always recognized, including future servers.
# Aliases cover common requests that name the task rather than its application.
SERVER_ALIASES = {
    "blender": ("blender", "bpy", "geometry nodes", "cycles", "eevee",
                "z-anatomy", "z anatomy", "z-anatomie", "z anatomy3d",
                "humgen", "humgen3d", "human generator"),
    "unity": ("unity", "unity3d", "unity 6", "gameobject", "prefab", "urp"),
    "ableton": ("ableton", "ableton live", "session live"),
    "reaper": ("reaper", "reascript"),
    "gmail": ("gmail", "email", "e-mail", "emails", "mail", "mails", "courriel", "courriels"),
    "web": ("web", "internet", "recherche en ligne", "cherche en ligne", "meteo", "actualites"),
    "vocalbrain": ("vocalbrain", "rvc", "clone vocal", "clonage vocal", "conversion vocale"),
    "klodymusic": ("klodymusic", "klody music", "accords", "harmonie"),
    "gadget": ("gadget", "forge", "libretto"),
    "dream-x-world": ("dream-x-world", "lore", "worldbuilding", "univers de fiction", "monde de fiction"),
    "samplebrain": ("samplebrain", "samples", "echantillons audio"),
    "atelier": ("atelier", "analyser_morceau", "analyse ce morceau", "analyser ce morceau",
                "analyse le morceau", "ecoute ce morceau", "ecouter ce morceau", "morceau joint",
                "morceaux audio joints", "stems", "separe les stems", "separer les stems",
                "tempo du morceau", "bpm du morceau", "tonalite du morceau", "accords du morceau",
                "structure du morceau", "groove du morceau"),
    "sphero": ("sphero", "rvr", "boulon robot"),
    "vlc": ("vlc",),
}

NATIVE_GROUPS = {
    "code": set(["write_file", "execute_command", "run_in_sandbox", "find_symbol", "find_references", "find_relevant_files", "analyze_dependencies", "code_graph", "create_project", "open_in_pycharm", "preview_code", "preview_file", "list_previews", "stop_preview_server", "git_control", "generate_uml", "scaffold_api", "scaffold_sdk", "scaffold_nosql"]),
    "github": set(["browse_repo", "read_github_file", "list_indexed_repos", "index_github_repo", "extract_best_practices", "clone_github_repo"]),
    "library": set(["await_distillation", "import_llm_export", "list_imports", "search_books", "library_catalog", "get_skills", "learn_from_books", "distill_theme",
                     "library_web_search", "library_import_source", "library_refresh_source", "library_source_status"]),
    "skills": set(["list_skills", "save_skill", "delete_skill"]),
    "audio": set(["analyze_audio", "edit_wav", "mix_stems", "generate_silence", "convert_format", "get_waveform_data", "speak"]),
    "documents": set(["generate_excel", "generate_text_file", "bundle_zip"]),
    "images": {"analyser_image"},
    "desktop": set(["run_applescript", "spotlight_search", "list_shortcuts", "run_shortcut", "reveal_in_finder"]),
    "home": {"mqtt_publish", "mqtt_subscribe"},
    "files": set(["write_file", "batch_rename", "organize_directory", "backup_directory", "sync_directories"]),
    "infrastructure": set(["run_sql", "docker_control", "kubectl_control", "scaffold_tool", "list_tool_kinds"]),
}
GROUP_ALIASES = {
    "code": ("code", "coder", "script", "scripts", "python", "bpy", "c#", "javascript", "typescript", "html", "css", "react", "fastapi", "bug", "corrige", "debug", "refactor", "tests", "pytest", "depot", "repository", "repo", "api", "fonction", "algorithme", "page web", "site web", "implementation", "implemente", "compile", "build", "git", "commit"),
    "github": ("github", "pull request", "clone", "cloner"),
    "library": ("librarybrain", "library brain", "livre", "livres", "ouvrage", "ouvrages", "corpus", "bibliotheque", "distille", "distiller", "distillation", "sources", "apprendre",
                "code de la route", "droit routier", "legifrance"),
    "skills": ("skill", "skills", "competence", "competences"),
    "audio": ("audio", "wav", "mp3", "mix", "mixage", "stems", "musique", "chanson", "midi", "ableton", "reaper"),
    "documents": ("excel", "xlsx", "csv", "document", "rapport", "zip", "texte"),
    "images": ("image", "photo", "capture", "screenshot"),
    "desktop": ("applescript", "raccourci", "raccourcis", "finder", "spotlight", "application", "applications"),
    "home": ("mqtt", "domotique", "maison connectee"),
    "files": ("fichier", "fichiers", "dossier", "dossiers", "renomme", "renommer", "sauvegarde", "backup", "synchronise"),
    "infrastructure": ("sql", "sqlite", "postgres", "database", "base de donnees", "docker", "kubernetes", "kubectl", "scaffold"),
}
CORE_TOOLS = {"read_file", "list_files", "search_in_files", "list_skills",
              "remember_fact", "forget_fact", "rappeler_memoire"}

# Unity 10.2 exposes 47 broad tools. Keep routine scene work small; every
# optional tool remains reachable by exact name or by asking for the full set.
UNITY_CORE = {"manage_scene", "find_gameobjects", "manage_gameobject", "manage_components",
              "manage_editor", "read_console", "refresh_unity", "batch_execute"}
UNITY_GROUPS = {
    ("animation", "animer", "personnage", "rig", "animator"): {"manage_animation"},
    ("camera", "cinematique", "cinemachine"): {"manage_camera"},
    ("materiau", "materiaux", "texture", "couleur", "rendu", "eclairage", "shader", "graphique"): {"manage_material", "manage_texture", "manage_shader", "manage_graphics"},
    ("physique", "physics", "collision", "rigidbody", "gravite"): {"manage_physics"},
    ("prefab", "prefabs", "asset", "import", "importer", "fbx"): {"manage_prefabs", "manage_asset", "import_model_file"},
    ("ui", "interface", "bouton", "menu", "uxml", "uss"): {"manage_ui"},
    ("vfx", "particules", "effets"): {"manage_vfx"},
    ("script", "scripts", "code", "c#", "bug", "corrige", "compile"): {"execute_code", "create_script", "manage_script", "script_apply_edits", "validate_script", "apply_text_edits", "find_in_file", "get_sha", "manage_script_capabilities", "unity_reflect", "unity_docs"},
    ("documentation", "docs", "api"): {"unity_docs", "unity_reflect"},
    ("tests", "test", "pytest"): {"run_tests", "get_test_job"},
    ("profiler", "profiling", "performance", "performances", "fps", "memoire"): {"manage_profiler"},
    ("build", "compile", "livre", "livrer", "export", "exporte"): {"manage_build"},
    ("package", "packages", "installer"): {"manage_packages"},
    ("probuilder",): {"manage_probuilder"},
    ("generation ia", "generateur ia"): {"generate_model", "generate_image", "generate_audio", "import_model"},
}


def select_mcp_tools(query: str, tools: list[dict]) -> list[dict]:
    text = normalize(query)
    selected = set(UNITY_CORE)
    for aliases, names in UNITY_GROUPS.items():
        if matches(text, aliases):
            selected.update(names)
    include_all = matches(text, ("tous les outils", "all tools", "catalogue complet"))
    result = []
    for tool in tools:
        name = tool["function"]["name"]
        if not name.startswith("mcp__unity__"):
            result.append(tool)
            continue
        short_name = name.removeprefix("mcp__unity__")
        if not include_all and short_name not in selected and not matches(text, (name, short_name)):
            continue
        # Upstream middleware supports per-call targeting but omits it from the
        # schema. Klody opens a fresh MCP session per call, so advertise this
        # option instead of relying on set_active_instance being sticky.
        tool = deepcopy(tool)
        tool["function"]["parameters"].setdefault("properties", {})["unity_instance"] = {
            "type": "string", "description": "Projet Unity cible sous la forme Name@hash. Répéter à chaque appel si plusieurs éditeurs sont ouverts ; omettre seulement si une unique instance est connectée."
        }
        result.append(tool)
    return result


def select_servers(query: str, available: set[str]) -> set[str]:
    text = normalize(query)
    # These model names are not the 3D application.
    text = re.sub(r"\bllm[- ]blender\b|\bblenderbot\b", "", text)
    selected = {name for name in available
                if matches(text, (normalize(name), *SERVER_ALIASES.get(name, ())))}
    # The established music production workflow requires these three services.
    # An explicit Ableton request must not accidentally select REAPER too.
    production = matches(text, ("compose", "composer", "cree une chanson", "produis", "produire", "cree un morceau", "cree une musique"))
    musical = matches(text, ("musique", "chanson", "morceau", "midi", "instrumental", "beat"))
    if production and musical and not selected.intersection({"ableton", "reaper"}):
        selected.update(available.intersection({"reaper", "gadget", "klodymusic"}))
    if "reaper" in selected and production and musical:
        selected.update(available.intersection({"gadget", "klodymusic"}))
    return selected


def select_native(query: str, tools: list[dict]) -> set[str]:
    text = normalize(query)
    selected = set(CORE_TOOLS)
    for group, aliases in GROUP_ALIASES.items():
        if matches(text, (group, *aliases)):
            selected.update(NATIVE_GROUPS[group])
    # Exact tool references work even for future native tools with no group.
    for tool in tools:
        name = tool["function"]["name"]
        if not name.startswith("mcp__") and matches(text, (name,)):
            selected.add(name)
    return selected


def context_query(query: str, messages: list[dict], available: set[str]) -> str:
    """Carry a capability through a follow-up, including after session reload."""
    text = normalize(query).strip()
    if not text or select_servers(query, available):
        return query
    followup = re.match(r"^(oui|ok|vas[ -]y|continue|poursuis|encore|et\b|puis\b|maintenant\b|mets\b|change\b|modifie\b|ajoute\b|retire\b|annule\b|remets\b|recommence\b|reessaie\b|fais\b)", text)
    # A new explicit topic supersedes the previous app. File/code operations
    # such as 'ajoute un bevel' can still continue work in that application.
    if not followup or len(text.split()) > 30:
        return query
    if matches(text, ("github", "email", "unity", "sql", "docker", "livre", "livres")):
        return query
    for message in reversed(messages[-12:]):
        if message.get("role") != "user":
            continue
        previous = message.get("content") or ""
        if not isinstance(previous, str) or previous == query:
            continue
        if select_servers(previous, available):
            # Preserve application intent, without repeating a potentially huge prompt.
            names = sorted(select_servers(previous, available))
            return " ".join(names) + "\n" + query
        if matches(normalize(previous), GROUP_ALIASES["code"]):
            return "code\n" + query
        # Do not jump past a newer unrelated user request into an old app.
        if len(previous.split()) > 4:
            break
    return query


def capability_catalog(servers: set[str]) -> str:
    return ("Groupes natifs : " + ", ".join(NATIVE_GROUPS) +
            ". Serveurs MCP configurés (disponibilité à vérifier) : " +
            (", ".join(sorted(servers)) or "aucun") + ".")


def capability_guidance(servers: set[str]) -> str:
    if "blender" not in servers:
        return ""
    return (
        "\n\n## Bibliothèques d'assets Blender\n"
        "Z-Anatomy est une bibliothèque d'assets, pas un add-on Python. "
        "Vérifier sa présence avec mcp__blender__list_asset_library_assets ; "
        "une absence dans preferences.addons ne prouve rien. "
        "L'import par script est possible : appeler mcp__blender__import_asset_collection "
        "avec collection_name et blend_file exacts de la liste. "
        "L'API native est bpy.data.libraries.load(link=False, assets_only=True), "
        "puis scene.collection.children.link(collection). "
        "Ne pas imposer d'import manuel ou inventer une API Z-Anatomy. "
        "Inspecter puis importer uniquement ce qui est demandé, préserver la scène "
        "et vérifier le résultat. Si ces outils manquent, appeler find_tools('blender')."
    )


FIND_TOOLS = {"type": "function", "function": {
    "name": "find_tools",
    "description": "Recherche et charge les outils utiles à une tâche, un groupe ou une application. À utiliser si un outil manque. Les outils trouvés deviennent appelables au tour suivant. Une requête vide donne le catalogue, sans lancer tous les serveurs.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}}
READ_SKILL = {"type": "function", "function": {
    "name": "read_skill",
    "description": "Lit intégralement une compétence sauvegardée par son slug, avec ses références. À appeler avant d'appliquer une méthode indiquée dans l'index des compétences.",
    "parameters": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]},
}}

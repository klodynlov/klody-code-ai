"""Politique d'approbation humaine des outils (human-in-the-loop).

Côté API/WebSocket, les outils qui ont un *effet de bord* — écriture ou
suppression de fichiers, exécution de commandes/code, mutations externes
(GitHub, serveurs MCP) — requièrent une validation explicite de
l'utilisateur avant exécution. Les outils en lecture seule (lecture,
recherche, inspection, preview) passent sans interruption.

`requires_approval` est une fonction *pure* : testable unitairement et
partagée entre le serveur WebSocket et d'éventuels autres points d'entrée.
Le CLI garde son propre garde-fou interactif (cf. tools/terminal.py) ; ce
module ne concerne que le chemin non-interactif (l'app desktop / l'API).
"""
from __future__ import annotations

# Outils internes (cœur Klody) à effet de bord → validation requise.
_SIDE_EFFECT_TOOLS: frozenset[str] = frozenset({
    # Écritures / suppressions sur le disque
    "write_file", "save_skill", "delete_skill", "import_llm_export",
    # Exécution de commandes shell ou de code arbitraire
    "execute_command", "run_in_sandbox",
    # Réseau + disque (clone, indexation, scaffolding de projet)
    "clone_github_repo", "index_github_repo", "create_project",
    # Création d'un skill permanent à partir de livres
    "learn_from_books", "distill_theme",
    # Production d'artefacts audio sur le disque
    "edit_wav", "mix_stems", "generate_silence", "convert_format",
})
# NB : generate_excel / generate_text_file / bundle_zip ne sont PAS gardés — leurs
# artefacts sont confinés à DOWNLOADS_DIR (nom basenamé, aucune écriture possible
# hors de ce dossier) et l'utilisateur les a explicitement demandés. Les garde-fous
# anti-traversée vivent dans tools/excel.py, tools/documents.py et tools/archive.py.
# Même logique pour speak : WAV confiné à ~/.vocalbrain/audio, lecture demandée
# par l'utilisateur, quelques secondes — pas de validation.

# Outils MCP : on classe par le VERBE DE TÊTE du nom (mcp__serveur__verbe_objet).
# C'est plus fiable qu'une recherche de sous-chaîne : « list_labels » (lecture)
# ne doit pas matcher à cause de « label », alors que « label_message » (écriture)
# le doit. Verbe de tête mutateur → validation. Les serveurs MCP maison nomment
# leurs outils en FRANÇAIS (generer_chanson, lister_voix…) → les deux langues
# sont couvertes, sans accents (les noms d'outils n'en portent pas).
_WRITE_VERBS: frozenset[str] = frozenset({
    "create", "update", "delete", "remove", "send", "write", "commit",
    "push", "merge", "upload", "move", "draft", "reply", "respond",
    "label", "unlabel", "edit", "insert", "export", "import", "schedule",
    "cancel", "add", "set", "copy", "generate", "resize", "perform",
    "comment", "post", "put", "patch", "rename", "replace", "archive",
    "trash", "star", "modify", "start", "stop", "make", "save", "apply",
    # verbes français (serveurs MCP maison : vocalbrain, gmail…)
    "generer", "entrainer", "creer", "supprimer", "envoyer", "ecrire",
    "modifier", "ajouter", "deplacer", "renommer", "remplacer", "lancer",
})
_READ_VERBS: frozenset[str] = frozenset({
    "list", "get", "search", "read", "fetch", "find", "show", "describe",
    "suggest", "resolve", "help", "download", "view", "lookup", "query",
    "count", "check", "status", "preview", "inspect", "snapshot", "logs",
    # verbes français
    "lister", "lire", "chercher", "rechercher", "afficher", "etat",
    "statut", "resultat", "voir", "consulter",
})
# Verbes franchement destructeurs : si présents n'importe où dans le nom,
# on valide même quand le verbe de tête est inconnu.
_STRONG_WRITE_VERBS: frozenset[str] = frozenset({
    "create", "update", "delete", "remove", "send", "write", "commit",
    "push", "merge", "upload",
})


def requires_approval(tool_name: str) -> bool:
    """True si `tool_name` doit être validé par l'utilisateur avant exécution.

    Politique « actions à effet de bord » : on garde-fou les écritures, les
    exécutions et les mutations externes ; la lecture/recherche/inspection
    passe librement.
    """
    if tool_name in _SIDE_EFFECT_TOOLS:
        return True
    if not tool_name.startswith("mcp__"):
        return False

    leaf = tool_name.rsplit("__", 1)[-1].lower().replace("-", "_")
    tokens = [t for t in leaf.split("_") if t]
    head = tokens[0] if tokens else ""
    if head in _WRITE_VERBS:
        return True
    if head in _READ_VERBS:
        return False
    # Verbe de tête inconnu : on valide si un verbe franchement mutateur
    # apparaît ailleurs dans le nom, sinon on laisse passer (lecture probable).
    return any(tok in _STRONG_WRITE_VERBS for tok in tokens)

"""Registre central des outils — schémas JSON Schema compatibles OpenAI function calling."""

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Lit le contenu d'un fichier dans le répertoire projet. "
                "À appeler avant toute modification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin relatif depuis la racine du projet (ex: src/main.py)",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Écrit ou remplace le contenu complet d'un fichier. "
                "Toujours lire le fichier avant d'écrire pour ne rien perdre."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin relatif depuis la racine du projet",
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenu complet à écrire dans le fichier",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Liste les fichiers et dossiers dans un répertoire du projet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin relatif du répertoire à lister (défaut: racine)",
                        "default": ".",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Si true, liste récursivement tous les sous-dossiers",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": (
                "Exécute une commande shell dans le répertoire projet. "
                "Requiert une confirmation humaine explicite avant exécution. "
                "Toujours renseigner 'reason' pour expliquer le besoin."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Commande shell à exécuter",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Explication claire de pourquoi cette commande est nécessaire",
                    },
                },
                "required": ["command", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": (
                "Recherche un pattern (texte ou regex) dans les fichiers du projet. "
                "Utilise ripgrep si disponible, sinon grep."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Pattern de recherche, supporte les expressions régulières",
                    },
                    "path": {
                        "type": "string",
                        "description": "Répertoire où chercher (défaut: racine du projet)",
                        "default": ".",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Filtre glob sur les noms de fichiers (ex: '*.py')",
                        "default": "",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Recherche sensible à la casse",
                        "default": True,
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


LIST_SKILLS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": (
            "Liste toutes les compétences mémorisées (user skills). "
            "Appelle cet outil pour savoir ce qui a déjà été appris avant de sauvegarder un doublon, "
            "ou pour répondre à 'quelles compétences as-tu ?'."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

DELETE_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_skill",
        "description": (
            "Supprime une compétence mémorisée par son slug. "
            "Utilise list_skills d'abord pour obtenir le slug exact."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Slug de la compétence à supprimer (ex: 'commit_convention')",
                },
            },
            "required": ["slug"],
        },
    },
}

SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "save_skill",
        "description": (
            "Sauvegarde une compétence, un pattern ou un snippet utile pour les "
            "prochaines sessions. Utilise cet outil quand tu produis quelque chose "
            "de réutilisable : un pattern de code, une solution à un problème récurrent, "
            "une configuration type, une bonne pratique identifiée."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nom court de la compétence (ex: 'Jeu Python devinette')",
                },
                "description": {
                    "type": "string",
                    "description": "Ce que fait cette compétence et quand l'utiliser",
                },
                "content": {
                    "type": "string",
                    "description": "Le code, pattern ou connaissance à mémoriser",
                },
            },
            "required": ["name", "description", "content"],
        },
    },
}

IMPORT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "import_llm_export",
            "description": (
                "Lit et analyse un export JSON d'un autre LLM (ChatGPT, Claude, Gemini…). "
                "Détecte automatiquement le format, extrait les messages utilisateur, "
                "identifie les technologies et pratiques récurrentes. "
                "Utilise cet outil pour enrichir ta connaissance des habitudes de l'utilisateur. "
                "Les fichiers doivent être déposés dans le dossier imports/ du projet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Nom du fichier JSON à analyser (ex: 'conversations.json'). "
                            "Chemin relatif depuis imports/ ou chemin absolu."
                        ),
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_imports",
            "description": (
                "Liste les fichiers d'export LLM disponibles dans le dossier imports/. "
                "Appelle cet outil avant import_llm_export pour voir ce qui est disponible."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

MCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_books",
            "description": (
                "Recherche des extraits pertinents dans la bibliothèque locale LibraryBrain "
                "(livres techniques indexés via RAG). Utilise cet outil quand la question "
                "porte sur un sujet où un livre de référence peut aider : architecture, "
                "patterns, algorithmes, frameworks. "
                "Retourne les passages les plus pertinents avec leur source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Question ou sujet à rechercher dans les livres indexés",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de passages à retourner (1-5, défaut: 3)",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_skills",
            "description": (
                "Récupère les conventions et patterns techniques d'un domaine spécifique. "
                "Utilise cet outil avant de générer du code pour respecter les conventions "
                "du projet dans ce domaine. "
                "Domaines disponibles : symfony, nextjs, python, mlx."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domaine technique cible",
                        "enum": ["symfony", "nextjs", "python", "mlx"],
                    },
                },
                "required": ["domain"],
            },
        },
    },
]

TOOLS = [*TOOLS, LIST_SKILLS_TOOL, DELETE_SKILL_TOOL, SKILL_TOOL, *IMPORT_TOOLS, *MCP_TOOLS]


def get_tools() -> list[dict]:
    return TOOLS


def get_tool_names() -> list[str]:
    return [t["function"]["name"] for t in TOOLS]

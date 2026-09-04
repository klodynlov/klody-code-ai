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
                "Exécute une commande shell dans le répertoire projet. Requiert "
                "confirmation humaine. Renseigner 'reason'."
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
            "name": "await_distillation",
            "description": (
                "Attend la fin d'une distillation en arrière-plan et renvoie son verdict "
                "(done/refused/error). Appel bloquant côté serveur (jusqu'à ~30 min)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "RUN_ID renvoyé par klody-distill.sh start.",
                    },
                    "timeout_s": {
                        "type": "integer",
                        "description": (
                            "Attente max en secondes (défaut 1800). Au-delà, "
                            "renvoie 'running' pour rappeler l'outil."
                        ),
                    },
                },
                "required": ["run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": (
                "Cherche les définitions d'un symbole (fonction, classe, méthode) dans le "
                "projet via tree-sitter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nom exact du symbole (case-sensitive). Ex: 'Router', 'compute_area'",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": (
                "Liste tous les endroits où un symbole est utilisé/appelé. "
                "Indispensable avant de renommer ou refactorer une fonction "
                "pour ne rien casser. Retourne fichier:ligne + contexte."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nom exact du symbole à chercher (case-sensitive)",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_relevant_files",
            "description": (
                "Recherche sémantique : fichiers du projet les plus pertinents pour une "
                "question en langage naturel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Question ou intention en langage naturel (français OK)",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Nombre de fichiers à retourner (défaut: 5, max raisonnable: 10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_in_sandbox",
            "description": (
                "Exécute une commande Python (pytest, python, pip) dans un venv jetable. "
                "Récupère stdout/stderr/exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Commande à exécuter, ex: 'pytest test_x.py -q' ou 'python main.py'. "
                            "Les chemins sont relatifs au workdir. python/pytest/pip sont remappés "
                            "vers le venv sandbox automatiquement."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout en secondes (défaut: 30)",
                        "default": 30,
                    },
                    "workdir": {
                        "type": "string",
                        "description": (
                            "Optionnel : répertoire d'exécution (relatif au projet ou "
                            "absolu sous une racine autorisée). Par défaut le projet courant. "
                            "Utile pour tester du code écrit dans un autre projet."
                        ),
                    },
                },
                "required": ["command"],
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
        "description": "Liste toutes les compétences mémorisées (user skills).",
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
            "Sauvegarde une compétence, un pattern ou un snippet réutilisable pour les "
            "prochaines sessions."
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
                "code_compatible": {
                    "type": "boolean",
                    "description": (
                        "Optionnel (défaut false). Mets true UNIQUEMENT si ce skill est "
                        "utile à la GÉNÉRATION DE CODE (convention de code projet, pattern "
                        "framework). Il pourra alors être injecté — compact — au modèle "
                        "coder. Laisse false pour un skill conceptuel/explicatif."
                    ),
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
                "Lit un export JSON d'un autre LLM (ChatGPT, Claude, Gemini…). Détecte le "
                "format, extrait les messages, identifie technologies récurrentes."
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
            "description": "Liste les fichiers d'export LLM disponibles dans imports/.",
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
                "Interroge LibraryBrain (RAG multi-livres) : réponse sourcée (citations "
                "livre + page) à partir du contenu des livres indexés. Peut prendre 1-3 "
                "min."
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
            "name": "library_catalog",
            "description": (
                "Cherche un livre au catalogue LibraryBrain par titre ou auteur "
                "(métadonnée, instantané). Ne cherche pas dans le contenu."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Titre, auteur ou mots-clés du titre à retrouver",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de livres à retourner (1-10, défaut: 5)",
                        "default": 5,
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
                "Récupère les conventions et patterns techniques d'un domaine (symfony, "
                "nextjs, python, mlx, claude_code, graphql, docker, kubernetes, cicd, sdk, "
                "uml, sql)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domaine technique cible",
                        "enum": [
                            "symfony", "nextjs", "python", "mlx", "claude_code",
                            "graphql", "docker", "kubernetes", "cicd", "sdk", "uml", "sql",
                        ],
                    },
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "learn_from_books",
            "description": "Apprend un sujet depuis LibraryBrain et le mémorise comme compétence permanente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Sujet à apprendre (ex: 'design patterns Python', 'optimisation SQL')",
                    },
                    "skill_name": {
                        "type": "string",
                        "description": "Nom de la compétence créée (auto-généré si vide)",
                        "default": "",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "distill_theme",
            "description": (
                "Distille un thème entier depuis LibraryBrain (multi-livres) en une "
                "compétence digest structurée. Classe les livres par pertinence, moissonne "
                "les extraits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "description": "Thème à distiller (ex: 'optimisation WebGL', 'design d'API REST')",
                    },
                    "slug": {
                        "type": "string",
                        "description": "Corps du slug (auto depuis le thème si vide) — sera préfixé digest_",
                        "default": "",
                    },
                    "code_compatible": {
                        "type": "boolean",
                        "description": "true si le thème sert des tâches de CODE (le digest sera aussi injecté, compact, au modèle coder)",
                        "default": False,
                    },
                },
                "required": ["theme"],
            },
        },
    },
]

MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": (
                "Mémorise un fait important entre les sessions (préférence, projet, info "
                "utilisateur). Met à jour si la clé existe."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": (
                            "Identifiant court en snake_case "
                            "(ex: 'style_code', 'projet_principal', 'langage_prefere')"
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenu à mémoriser — une phrase claire et concise",
                    },
                    "category": {
                        "type": "string",
                        "description": "Catégorie du fait",
                        "enum": ["user", "project", "preference", "context"],
                        "default": "context",
                    },
                },
                "required": ["key", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_fact",
            "description": (
                "Supprime un fait mémorisé par sa clé. "
                "Utilise cet outil quand une information est obsolète ou incorrecte."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Clé du fait à oublier (snake_case)",
                    },
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rappeler_memoire",
            "description": (
                "Recherche sémantique dans la mémoire archivée : faits mémorisés et "
                "sessions passées. Recherche en langage naturel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "requete": {
                        "type": "string",
                        "description": "Ce qu'on cherche, en langage naturel (ex: 'décision sur le backend MLX')",
                    },
                    "nombre": {
                        "type": "integer",
                        "description": "Nombre de souvenirs à ramener (défaut 5, max 20)",
                        "default": 5,
                    },
                    "type": {
                        "type": "string",
                        "description": (
                            "Filtre optionnel par type de souvenir : "
                            "user, project, preference, context ou session. Vide = tous."
                        ),
                        "default": "",
                    },
                },
                "required": ["requete"],
            },
        },
    },
]

GITHUB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browse_repo",
            "description": (
                "Parcourt l'arbre de fichiers d'un dépôt GitHub. Accepte 'owner/repo' ou "
                "une URL complète."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Dépôt GitHub (ex: 'fastapi/fastapi' ou URL complète)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Sous-dossier à explorer (défaut: racine)",
                        "default": "",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Si true, affiche tout l'arbre récursivement",
                        "default": False,
                    },
                },
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_github_file",
            "description": "Lit le contenu d'un fichier source depuis un dépôt GitHub distant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Dépôt GitHub (ex: 'owner/repo')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Chemin du fichier dans le dépôt (ex: 'src/main.py')",
                    },
                },
                "required": ["repo", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_indexed_repos",
            "description": "Liste les dépôts GitHub indexés dans LibraryBrain.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "index_github_repo",
            "description": (
                "Indexe un dépôt GitHub dans LibraryBrain (README + docs) pour "
                "l'interroger avec search_books."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Dépôt GitHub à indexer (ex: 'owner/repo')",
                    },
                },
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_best_practices",
            "description": (
                "Analyse un dépôt GitHub et en extrait les bonnes pratiques : structure, "
                "outils, CI/CD, linting, dépendances."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Dépôt GitHub à analyser (ex: 'owner/repo')",
                    },
                },
                "required": ["repo"],
            },
        },
    },
]

PROJECT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "clone_github_repo",
            "description": "Clone un dépôt GitHub dans le dossier projets et l'ouvre dans PyCharm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Dépôt GitHub (ex: 'owner/repo')",
                    },
                    "target_dir": {
                        "type": "string",
                        "description": "Dossier de destination (optionnel, défaut: PROJECTS_DIR/repo)",
                        "default": "",
                    },
                },
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": (
                "Crée un projet local depuis un template (python, fastapi, cli, empty) et "
                "l'ouvre dans PyCharm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nom du projet (sera le nom du dossier)",
                    },
                    "template": {
                        "type": "string",
                        "description": "Type de template",
                        "enum": ["python", "fastapi", "cli", "empty"],
                        "default": "python",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description courte du projet",
                        "default": "",
                    },
                    "inspired_by": {
                        "type": "string",
                        "description": "Dépôt GitHub source d'inspiration (ex: 'owner/repo')",
                        "default": "",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_in_pycharm",
            "description": (
                "Ouvre un dossier de projet dans PyCharm. "
                "Utilise cet outil après un clone ou quand l'utilisateur veut ouvrir un projet existant."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Chemin absolu ou relatif du dossier à ouvrir",
                    },
                },
                "required": ["project_path"],
            },
        },
    },
]

PREVIEW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "preview_code",
            "description": (
                "Génère un aperçu local de code HTML/CSS/JS via un serveur HTTP. "
                "Librairies externes : déclarer l'URL CDN dans 'scripts'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "html": {
                        "type": "string",
                        "description": (
                            "Contenu HTML. De préférence le fragment du <body> seul "
                            "(sans <!DOCTYPE>/<html>/<head>), mais un document HTML complet "
                            "est aussi accepté et servi tel quel — ne le mets jamais deux fois."
                        ),
                    },
                    "css": {
                        "type": "string",
                        "description": "Code CSS à injecter dans une balise <style>",
                        "default": "",
                    },
                    "js": {
                        "type": "string",
                        "description": "Code JavaScript à injecter dans une balise <script>",
                        "default": "",
                    },
                    "title": {
                        "type": "string",
                        "description": "Titre de la page (utilisé aussi pour le nom du fichier)",
                        "default": "Preview",
                    },
                    "scripts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "URLs CDN des librairies JS externes à charger AVANT ton code "
                            "(ex: ['https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js']). "
                            "Indispensable dès que js référence THREE, Chart, d3, p5…"
                        ),
                        "default": [],
                    },
                    "styles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URLs CDN de feuilles de style externes à charger (<link>).",
                        "default": [],
                    },
                },
                "required": ["html"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_file",
            "description": (
                "Ouvre un fichier HTML existant du projet dans le navigateur via le "
                "serveur de prévisualisation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin relatif du fichier HTML à prévisualiser",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_previews",
            "description": (
                "Liste tous les aperçus HTML disponibles dans le dossier de prévisualisation "
                "avec leurs URLs locales."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_preview_server",
            "description": "Arrête le serveur HTTP de prévisualisation.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

AUDIO_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "analyze_audio",
            "description": (
                "Analyse un fichier audio : durée, BPM, tonalité estimée, "
                "RMS/peak en dB, sample rate, nombre de canaux. Lecture seule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin (relatif au projet ou absolu sous une racine autorisée) vers le fichier audio.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_wav",
            "description": (
                "Édite un fichier audio : trim (start/end), fade in/out, "
                "normalisation. Écrit dans `output` (défaut: écrase l'original)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Fichier audio source."},
                    "start": {"type": "number", "description": "Début en secondes (défaut 0)."},
                    "end": {"type": "number", "description": "Fin en secondes (défaut = fin du fichier)."},
                    "fade_in": {"type": "number", "description": "Durée du fade in en secondes."},
                    "fade_out": {"type": "number", "description": "Durée du fade out en secondes."},
                    "normalize": {"type": "boolean", "description": "Normaliser le peak à -1.0."},
                    "output": {"type": "string", "description": "Chemin de sortie (défaut: écrase l'entrée)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mix_stems",
            "description": (
                "Mixe plusieurs fichiers audio (stems) en un seul, avec gains "
                "en dB optionnels par stem. Resample auto au sample rate du 1er."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste de chemins audio à mixer.",
                    },
                    "gains": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Gain en dB par stem (même longueur que `paths`). Défaut: 0 dB partout.",
                    },
                    "output": {"type": "string", "description": "Chemin de sortie (défaut: mixed_output.wav)."},
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_silence",
            "description": "Crée un fichier de silence de la durée demandée.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration": {"type": "number", "description": "Durée en secondes."},
                    "sr": {"type": "integer", "description": "Sample rate (défaut 44100)."},
                    "output": {"type": "string", "description": "Chemin de sortie (défaut silence.wav)."},
                },
                "required": ["duration"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_format",
            "description": (
                "Convertit un fichier audio vers un autre format (wav, mp3, "
                "flac, ogg). Utilise librosa si dispo, sinon ffmpeg en fallback."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Fichier audio source."},
                    "target_format": {"type": "string", "description": "Format cible (wav|mp3|flac|ogg). Défaut wav."},
                    "output": {"type": "string", "description": "Chemin de sortie (défaut: même stem, nouvelle extension)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_waveform_data",
            "description": (
                "Extrait des valeurs RMS downsamplées pour visualiser la "
                "waveform d'un fichier audio (utile pour rendu graphique)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Fichier audio source."},
                    "num_points": {"type": "integer", "description": "Nombre de points à retourner (défaut 256)."},
                },
                "required": ["path"],
            },
        },
    },
]

DOCUMENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "generate_excel",
            "description": (
                "Génère un classeur Excel (.xlsx) téléchargeable. Passe les données en "
                "feuilles (en-têtes + lignes)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Nom du fichier (ex: 'ventes_2026.xlsx'). L'extension .xlsx est forcée.",
                    },
                    "sheets": {
                        "type": "array",
                        "description": "Feuilles du classeur (au moins une).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Nom de l'onglet (ex: 'Ventes'). Défaut: Feuille1, 2…",
                                },
                                "columns": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "En-têtes de colonnes (ligne 1, mise en gras + figée).",
                                },
                                "rows": {
                                    "type": "array",
                                    "items": {"type": "array"},
                                    "description": (
                                        "Lignes de données : une liste de valeurs par ligne, "
                                        "alignées sur 'columns'."
                                    ),
                                },
                            },
                            "required": ["rows"],
                        },
                    },
                },
                "required": ["filename", "sheets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_text_file",
            "description": (
                "Génère un fichier texte ou code téléchargeable (.txt, .md, .csv, .json, "
                ".html, .py…). Passe le contenu complet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Nom du fichier avec extension (ex: 'notes.md', 'app.py', 'data.csv').",
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenu texte intégral du fichier.",
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bundle_zip",
            "description": (
                "Regroupe plusieurs fichiers texte/code dans un .zip téléchargeable. "
                "Fournis chemin relatif + contenu par fichier ; sous-dossiers conservés."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Nom de l'archive (ex: 'mon-projet.zip'). L'extension .zip est forcée.",
                    },
                    "files": {
                        "type": "array",
                        "description": "Fichiers à archiver (au moins un).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Chemin relatif dans l'archive (ex: 'src/App.tsx', 'README.md').",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Contenu texte du fichier.",
                                },
                            },
                            "required": ["name", "content"],
                        },
                    },
                },
                "required": ["filename", "files"],
            },
        },
    },
]

VOICE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": "Dit un court texte à voix haute (TTS local, voix de Klody). ≤ 600 caractères.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Texte à prononcer (court, naturel à l'oral).",
                    },
                    "language": {
                        "type": "string",
                        "description": "Code langue de la voix (fr, en, es…). Défaut : fr.",
                        "default": "fr",
                    },
                },
                "required": ["text"],
            },
        },
    },
]

IMAGE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "analyser_image",
            "description": (
                "Analyse une image locale avec le modèle vision (VL local). Répond à une "
                "question sur le contenu visuel. Formats : png, jpg, jpeg, webp, gif, bmp."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": (
                            "Chemin (relatif au projet ou absolu sous une racine "
                            "autorisée) vers le fichier image."
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Ce qu'on veut savoir sur l'image (ex. « quel texte ? », "
                            "« décris la maquette »). Défaut : description détaillée."
                        ),
                    },
                },
                "required": ["image_path"],
            },
        },
    },
]

CODE_GRAPH_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "code_graph",
            "description": (
                "Interroge le graphe de connaissance du code (relations entre symboles). "
                "Modes : overview, explain, callers, path. Lecture seule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["overview", "explain", "callers", "path"],
                        "description": "Opération. Défaut : explain.",
                        "default": "explain",
                    },
                    "symbol": {
                        "type": "string",
                        "description": (
                            "Nom du symbole cible (fonction/classe/méthode). "
                            "Requis sauf en mode `overview`. Pour `path`, c'est "
                            "le point de départ."
                        ),
                    },
                    "to": {
                        "type": "string",
                        "description": "Mode `path` uniquement : symbole d'arrivée.",
                    },
                },
                "required": [],
            },
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Pilotage de l'environnement (macOS Apple Silicon) — AppleScript, Spotlight,
# Raccourcis (HomeKit/Automator), Finder. Cf. tools/mac_control.py.
# ─────────────────────────────────────────────────────────────────────────────
MAC_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_applescript",
            "description": (
                "macOS. Exécute un AppleScript pour piloter une app scriptable. Les verbes "
                "destructeurs sont refusés."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": 'Source AppleScript, ex. tell application "Music" to play',
                    },
                    "reason": {
                        "type": "string",
                        "description": "Pourquoi cette automatisation (traçabilité).",
                    },
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spotlight_search",
            "description": (
                "macOS uniquement. Recherche indexée Spotlight (`mdfind`), lecture "
                "seule : retrouve fichiers/apps par nom ou contenu."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Requête Spotlight (texte, ou expression kMDItem…).",
                    },
                    "only_in": {
                        "type": "string",
                        "description": "Dossier où restreindre la recherche (optionnel).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Résultats max (défaut 20, plafond 200).",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_shortcuts",
            "description": (
                "macOS uniquement. Liste les Raccourcis Apple disponibles "
                "(`shortcuts list`), lecture seule."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shortcut",
            "description": (
                "macOS. Exécute un Raccourci Apple par son nom. Passerelle HomeKit, "
                "Automator et automatisations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nom exact du raccourci (voir list_shortcuts).",
                    },
                    "input_text": {
                        "type": "string",
                        "description": "Entrée texte passée au raccourci (optionnel).",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reveal_in_finder",
            "description": (
                "macOS uniquement. Révèle un fichier/dossier dans le Finder "
                "(`open -R`). Chemin confiné aux racines autorisées."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Chemin à révéler."},
                },
                "required": ["path"],
            },
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Maison connectée / IoT (MQTT) — ESP32, Raspberry Pi, Home Assistant, HomeKit
# via pont. Cf. tools/home_automation.py.
# ─────────────────────────────────────────────────────────────────────────────
HOME_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "mqtt_publish",
            "description": (
                "Publie un message MQTT (commande un appareil domotique). Broker local par "
                "défaut."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic MQTT, ex. maison/salon/lampe/set"},
                    "payload": {"type": "string", "description": "Charge utile, ex. ON ou {\"state\":\"on\"}"},
                    "host": {"type": "string", "description": "Broker (défaut : broker local)."},
                    "port": {"type": "integer", "description": "Port broker (défaut 1883)."},
                    "retain": {"type": "boolean", "description": "Message retenu (dernière valeur).", "default": False},
                    "qos": {"type": "integer", "description": "QoS MQTT 0/1/2 (défaut 0).", "default": 0},
                },
                "required": ["topic", "payload"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mqtt_subscribe",
            "description": "Écoute un topic MQTT pendant un temps borné et renvoie les messages reçus.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic/filtre, ex. maison/# ou capteurs/+/temp"},
                    "host": {"type": "string", "description": "Broker (défaut : broker local)."},
                    "port": {"type": "integer", "description": "Port broker (défaut 1883)."},
                    "timeout": {"type": "integer", "description": "Durée d'écoute max en s (plafond 60).", "default": 10},
                    "max_messages": {"type": "integer", "description": "Messages max (plafond 50).", "default": 10},
                },
                "required": ["topic"],
            },
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Automatisation de fichiers — renommage, organisation, sauvegarde, synchro.
# Sandboxé multi-racines. Cf. tools/automation.py.
# ─────────────────────────────────────────────────────────────────────────────
AUTOMATION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "batch_rename",
            "description": (
                "Renomme en lot les fichiers d'un dossier (motif → remplacement). "
                "dry_run=True par défaut : montre d'abord le plan. Fichiers sensibles "
                "ignorés."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Dossier cible."},
                    "pattern": {"type": "string", "description": "Sous-chaîne (ou regex) à trouver dans le nom."},
                    "replacement": {"type": "string", "description": "Texte de remplacement."},
                    "use_regex": {"type": "boolean", "description": "Interpréter pattern comme regex.", "default": False},
                    "recursive": {"type": "boolean", "description": "Descendre dans les sous-dossiers.", "default": False},
                    "dry_run": {"type": "boolean", "description": "Simuler sans renommer (défaut True).", "default": True},
                },
                "required": ["directory", "pattern", "replacement"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "organize_directory",
            "description": (
                "Range les fichiers d'un dossier dans des sous-dossiers, par type "
                "(catégorie d'extension) ou par date (AAAA-MM). dry_run=True par défaut."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Dossier à organiser."},
                    "by": {"type": "string", "enum": ["type", "date"], "description": "Critère (défaut type).", "default": "type"},
                    "dry_run": {"type": "boolean", "description": "Simuler sans déplacer (défaut True).", "default": True},
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backup_directory",
            "description": (
                "Crée une archive .tar.gz horodatée d'un dossier (sauvegarde). "
                "Destination par défaut : le dossier parent de la source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Dossier à sauvegarder."},
                    "destination": {"type": "string", "description": "Dossier où écrire l'archive (optionnel)."},
                },
                "required": ["source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_directories",
            "description": (
                "Copie incrémentale d'un dossier vers une destination. delete=True pour "
                "miroir. dry_run=True par défaut."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Dossier source (référence)."},
                    "destination": {"type": "string", "description": "Dossier destination (mis à jour)."},
                    "delete": {"type": "boolean", "description": "Supprimer de la dest les fichiers absents de la source.", "default": False},
                    "dry_run": {"type": "boolean", "description": "Simuler sans écrire (défaut True).", "default": True},
                },
                "required": ["source", "destination"],
            },
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Toolsmithing — Klody fabrique ses propres outils. Cf. tools/toolsmith.py.
# ─────────────────────────────────────────────────────────────────────────────
TOOLSMITH_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "scaffold_tool",
            "description": (
                "Fabrique un outil neuf et écrit ses fichiers. Kinds : python_script, cli, "
                "api, mcp_server, workflow, pipeline, klody_plugin, web_interface."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "python_script", "cli", "api", "mcp_server",
                            "workflow", "pipeline", "klody_plugin", "web_interface",
                        ],
                        "description": "Type d'artefact à générer.",
                    },
                    "name": {"type": "string", "description": "Nom de l'outil (→ dossier + identifiant assaini)."},
                    "target_dir": {"type": "string", "description": "Dossier parent (racine autorisée ; défaut : courant)."},
                    "description": {"type": "string", "description": "Courte description injectée dans les fichiers."},
                },
                "required": ["kind", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tool_kinds",
            "description": "Liste les types d'outils que Klody sait fabriquer (toolsmithing).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

DEPS_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "analyze_dependencies",
            "description": (
                "Inventorie les dépendances déclarées dans les manifestes d'un projet "
                "(lecture seule, aucun réseau). Reconnaît requirements*.txt, "
                "pyproject.toml, package.json, Cargo.toml, go.mod, composer.json."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Répertoire ou fichier manifeste (relatif au projet ou "
                            "absolu sous une racine autorisée). Défaut : racine du projet."
                        ),
                        "default": ".",
                    },
                },
                "required": [],
            },
        },
    },
]

SQL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Exécute une requête SQL sur un fichier SQLite local (sandbox). Lecture "
                "seule par défaut ; mode 'write' si activé. Une instruction par appel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "La requête SQL (une seule instruction). Ex: 'SELECT * FROM users WHERE id = ?'",
                    },
                    "database": {
                        "type": "string",
                        "description": (
                            "Chemin du fichier SQLite (relatif au projet ou absolu sous "
                            "une racine autorisée). Doit exister. Pas une URI."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "description": "'read' (défaut, lecture seule) ou 'write' (si activé côté serveur).",
                        "enum": ["read", "write"],
                        "default": "read",
                    },
                    "params": {
                        "type": "array",
                        "items": {},
                        "description": "Valeurs liées aux placeholders `?` de la requête (anti-injection).",
                        "default": [],
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Nombre max de lignes retournées (défaut 100, max 1000).",
                        "default": 100,
                    },
                },
                "required": ["query", "database"],
            },
        },
    },
]

DOCKER_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "docker_control",
            "description": (
                "Inspecte Docker local (lecture seule : ps, images, inspect, logs, stats, "
                "version, df). Mutation 'run' ultra-contraint si activé. Jamais "
                "build/exec/rm/stop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Opération (lecture seule, ou 'run' si l'écriture est activée).",
                        "enum": ["ps", "images", "inspect", "logs", "stats",
                                 "version", "df", "run"],
                    },
                    "target": {
                        "type": "string",
                        "description": (
                            "Nom ou ID du conteneur/image (requis pour 'inspect' et "
                            "'logs'). Charset strict : [a-zA-Z0-9 . _ - : /]."
                        ),
                        "default": "",
                    },
                    "image": {
                        "type": "string",
                        "description": "Pour 'run' : image à lancer (doit être dans l'allowlist serveur). Ex: python:3.12.",
                        "default": "",
                    },
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Pour 'run' : commande + args exécutés DANS le conteneur (ex: ['python','-c','print(1)']).",
                        "default": [],
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Pour 'logs' : nombre de dernières lignes (défaut 200, max 500).",
                        "default": 200,
                    },
                },
                "required": ["action"],
            },
        },
    },
]

K8S_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "kubectl_control",
            "description": (
                "Inspecte un cluster Kubernetes en lecture seule via kubectl. Actions : "
                "get, describe, logs, top, version, cluster-info, api-resources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Opération lecture seule.",
                        "enum": ["get", "describe", "logs", "top", "version",
                                 "cluster-info", "api-resources"],
                    },
                    "resource": {
                        "type": "string",
                        "description": "Type de ressource (ex: pods, deployments, svc). Requis pour get/describe/top.",
                        "default": "",
                    },
                    "name": {
                        "type": "string",
                        "description": "Nom de la ressource/pod (requis pour describe et logs).",
                        "default": "",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Namespace cible ('all' = tous). Défaut : namespace courant du contexte.",
                        "default": "",
                    },
                    "container": {
                        "type": "string",
                        "description": "Pour 'logs' : conteneur précis d'un pod multi-conteneurs.",
                        "default": "",
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Pour 'logs' : dernières lignes (défaut 200, max 500).",
                        "default": 200,
                    },
                },
                "required": ["action"],
            },
        },
    },
]

GIT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "git_control",
            "description": (
                "Inspecte un dépôt Git local (lecture seule : status, log, diff, show, "
                "blame, branch, tag, remote, shortlog). Mutations locales si activées : "
                "add, commit. Jamais push/pull/reset."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Opération (lecture seule, ou add/commit si l'écriture est activée).",
                        "enum": ["status", "log", "diff", "show", "blame",
                                 "branch", "tag", "remote", "shortlog", "add", "commit"],
                    },
                    "path": {
                        "type": "string",
                        "description": "Dossier du dépôt (relatif au projet ou absolu sous racine autorisée). Défaut : projet courant.",
                        "default": "",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Commit/branche/tag ou plage (ex: HEAD, main, a1b2c3, v1.0, main..dev).",
                        "default": "",
                    },
                    "file": {
                        "type": "string",
                        "description": "Fichier repo-relatif pour restreindre log/diff/blame (requis pour blame).",
                        "default": "",
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Pour 'log' : nombre de commits (défaut 20, max 200).",
                        "default": 20,
                    },
                    "message": {
                        "type": "string",
                        "description": "Message de commit (requis pour l'action 'commit').",
                        "default": "",
                    },
                },
                "required": ["action"],
            },
        },
    },
]

DIAGRAM_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "generate_uml",
            "description": (
                "Génère un diagramme de classes UML (Mermaid) depuis la structure réelle "
                "du code via tree-sitter. Python, JS, TS."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Dossier à diagrammer (relatif au projet ou absolu sous racine autorisée). Défaut : projet courant.",
                        "default": "",
                    },
                    "max_classes": {
                        "type": "integer",
                        "description": "Nombre max de classes dans le diagramme (défaut 40).",
                        "default": 40,
                    },
                },
                "required": [],
            },
        },
    },
]

SCAFFOLD_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "scaffold_api",
            "description": (
                "Génère un squelette d'API CRUD (FastAPI REST ou GraphQL Strawberry) à "
                "partir d'un nom de ressource et de champs typés."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {
                        "type": "string",
                        "description": "Nom de la ressource au singulier, minuscules (ex: 'user', 'product').",
                    },
                    "fields": {
                        "type": "array",
                        "description": "Champs du modèle (hors 'id', ajouté automatiquement).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Nom du champ (minuscules)."},
                                "type": {
                                    "type": "string",
                                    "description": "Type du champ.",
                                    "enum": ["str", "int", "float", "bool", "datetime"],
                                },
                            },
                            "required": ["name", "type"],
                        },
                        "default": [],
                    },
                    "framework": {
                        "type": "string",
                        "description": "Cible : 'fastapi' (REST) ou 'graphql' (schéma Strawberry).",
                        "enum": ["fastapi", "graphql"],
                        "default": "fastapi",
                    },
                },
                "required": ["resource"],
            },
        },
    },
]

SDK_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "scaffold_sdk",
            "description": "Génère un client SDK typé pour une API REST CRUD (dataclass + Client httpx).",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {
                        "type": "string",
                        "description": "Nom de la ressource au singulier, minuscules (ex: 'user').",
                    },
                    "fields": {
                        "type": "array",
                        "description": "Champs de la ressource (hors 'id', ajouté automatiquement).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Nom du champ (minuscules)."},
                                "type": {
                                    "type": "string",
                                    "description": "Type du champ.",
                                    "enum": ["str", "int", "float", "bool", "datetime"],
                                },
                            },
                            "required": ["name", "type"],
                        },
                        "default": [],
                    },
                    "language": {
                        "type": "string",
                        "description": "Langage du SDK (python uniquement pour l'instant).",
                        "enum": ["python"],
                        "default": "python",
                    },
                },
                "required": ["resource"],
            },
        },
    },
]

NOSQL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "scaffold_nosql",
            "description": (
                "Génère un repository MongoDB typé pour une ressource (dataclass + "
                "Repository pymongo CRUD)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {
                        "type": "string",
                        "description": "Nom de la ressource au singulier, minuscules (ex: 'user').",
                    },
                    "fields": {
                        "type": "array",
                        "description": "Champs de la ressource (l'_id Mongo est géré à part).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Nom du champ (minuscules)."},
                                "type": {
                                    "type": "string",
                                    "description": "Type du champ.",
                                    "enum": ["str", "int", "float", "bool", "datetime"],
                                },
                            },
                            "required": ["name", "type"],
                        },
                        "default": [],
                    },
                    "backend": {
                        "type": "string",
                        "description": "Backend NoSQL (mongodb uniquement pour l'instant).",
                        "enum": ["mongodb"],
                        "default": "mongodb",
                    },
                },
                "required": ["resource"],
            },
        },
    },
]

TOOLS = [*TOOLS, LIST_SKILLS_TOOL, DELETE_SKILL_TOOL, SKILL_TOOL, *IMPORT_TOOLS, *MCP_TOOLS, *MEMORY_TOOLS, *GITHUB_TOOLS, *PROJECT_TOOLS, *PREVIEW_TOOLS, *AUDIO_TOOLS, *DOCUMENT_TOOLS, *VOICE_TOOLS, *IMAGE_TOOLS, *CODE_GRAPH_TOOLS, *MAC_TOOLS, *HOME_TOOLS, *AUTOMATION_TOOLS, *TOOLSMITH_TOOLS, *DEPS_TOOLS, *SQL_TOOLS, *DOCKER_TOOLS, *K8S_TOOLS, *GIT_TOOLS, *DIAGRAM_TOOLS, *SCAFFOLD_TOOLS, *SDK_TOOLS, *NOSQL_TOOLS]
TOOLS.sort(key=lambda t: t["function"]["name"])


# Outil de question interactive — VOLONTAIREMENT hors de TOOLS/get_tools().
# Exposition conditionnelle : l'orchestrateur ne l'ajoute aux outils proposés au
# modèle que lorsqu'un skill INTERACTIF (QCM) est actif (_interactive_skill_active).
# Hors de ce cas, l'agent reste autonome (pas de questions sur une tâche de code).
# Le round-trip (pause du tour → carte cliquable côté UI → réponse) décalque la
# plomberie d'approbation humaine (cf. api/server.py _request_approval).
ASK_USER_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Pose UNE question à choix multiples à l'utilisateur et attend sa réponse "
            "(une fenêtre interactive cliquable s'affiche). À n'utiliser que pour cadrer "
            "un besoin (profilage QCM d'un skill interactif). Règle stricte : UNE seule "
            "question par appel ; attends la réponse avant de poser la suivante. Ne "
            "déverse jamais plusieurs questions d'un coup."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "La question, formulée clairement et de façon autonome.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Les choix proposés (un par entrée, ex. 'Trier / ordonner'). "
                        "Inclure une option « Autre / je ne sais pas » quand pertinent."
                    ),
                },
                "allow_free_text": {
                    "type": "boolean",
                    "description": (
                        "Autoriser une réponse libre en plus des options (défaut true). "
                        "Affiche un champ « Autre… » sous les boutons."
                    ),
                    "default": True,
                },
            },
            "required": ["question", "options"],
        },
    },
}


def get_tools() -> list[dict]:
    return TOOLS


def get_tool_names() -> list[str]:
    return [t["function"]["name"] for t in TOOLS]

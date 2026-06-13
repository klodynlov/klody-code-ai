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
            "name": "await_distillation",
            "description": (
                "Attend la fin d'une distillation lancée en arrière-plan via "
                "klody-distill.sh start, et renvoie son verdict final en UN seul "
                "appel : 'done <chemin.json>', 'refused <raison>' ou 'error "
                "<message>'. À utiliser À LA PLACE du polling répété de 'status' "
                "dans la boucle : l'attente se fait côté serveur, sans consommer "
                "d'itérations. Appel bloquant (jusqu'à ~30 min)."
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
                "Cherche où un symbole (fonction, classe, méthode) est défini dans "
                "le projet. Utilise cet outil avant de refactorer ou pour comprendre "
                "où vit une entité. Plus précis que search_in_files car il utilise "
                "tree-sitter et ne retourne que les définitions (pas les utilisations)."
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
                "Recherche sémantique : trouve les fichiers du projet les plus "
                "pertinents pour une question en langage naturel. Utilise cet outil "
                "quand tu ne sais pas dans quel(s) fichier(s) chercher. "
                "Ex: 'où est gérée l'authentification' → top fichiers triés par score."
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
                "Exécute une commande Python (pytest, python <fichier>, etc.) dans un "
                "venv jetable, en récupérant stdout/stderr/exit code. "
                "Utilise cet outil pour valider du code écrit : lancer les tests, "
                "vérifier qu'un script s'exécute, reproduire un bug. "
                "Après chaque write_file sur un .py, un check sandbox est lancé "
                "automatiquement — appelle cet outil uniquement pour des commandes spécifiques."
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
                "Interroge la bibliothèque locale LibraryBrain (RAG génératif multi-livres) : "
                "compose une RÉPONSE COMPLÈTE et sourcée (citations livre + page) à partir "
                "des livres indexés. Utilise cet outil quand la question porte sur un sujet "
                "où un livre de référence peut aider : architecture, patterns, algorithmes, "
                "frameworks, sciences. Peut prendre 1 à 3 minutes (génération locale)."
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
                "Domaines disponibles : symfony, nextjs, python, mlx, claude_code "
                "(claude_code = principes d'ingénierie, méthodes de debug/revue/test et workflow d'agent)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domaine technique cible",
                        "enum": ["symfony", "nextjs", "python", "mlx", "claude_code"],
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
            "description": (
                "Apprend un sujet et le mémorise comme compétence permanente. "
                "Utilise cet outil quand l'utilisateur veut enrichir tes connaissances sur un sujet, "
                "ou quand tu identifies un domaine où tu pourrais être plus compétent. "
                "Combine la recherche dans les livres (LibraryBrain) + les principes d'ingénierie "
                "pertinents du domaine claude_code + sauvegarde en skill réutilisable. "
                "Reste utile même si LibraryBrain est hors-ligne."
            ),
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
                "Distille un THÈME entier depuis la bibliothèque locale (DB Library "
                "Brain, multi-livres) en une compétence digest structurée et durable. "
                "Plus profond que learn_from_books : classe les livres du corpus par "
                "pertinence, moissonne les meilleurs extraits page par page, puis "
                "synthétise méthodes, pièges et boilerplate REFORMULÉS (jamais la prose "
                "des auteurs) dans skills/digest_<slug>.json. Utilise cet outil quand "
                "l'utilisateur demande de « distiller », « monter en compétences sur » "
                "ou « apprendre en profondeur » un domaine."
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
                "Mémorise un fait important entre les sessions. "
                "Utilise cet outil pour retenir une préférence, un projet en cours, "
                "ou une information sur l'utilisateur qui sera utile dans les futures sessions. "
                "Si la clé existe déjà, elle est mise à jour."
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
                "Recherche SÉMANTIQUE dans la mémoire archivée : tous les faits jamais "
                "mémorisés (même anciens, au-delà de ceux affichés dans le prompt) et les "
                "sessions passées. Utilise cet outil quand l'utilisateur fait référence à "
                "quelque chose de passé absent du contexte : « tu te souviens de… », "
                "« qu'avait-on décidé pour… », « sur quoi avait-on travaillé… ». "
                "Recherche en langage naturel, pas par clé exacte."
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
                "Parcourt l'arbre de fichiers d'un dépôt GitHub. "
                "Utilise cet outil pour voir la structure d'un projet avant de lire des fichiers. "
                "Accepte 'owner/repo' ou une URL GitHub complète."
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
            "description": (
                "Lit le contenu d'un fichier source depuis un dépôt GitHub. "
                "Utilise cet outil pour lire du code, des configs, ou de la documentation "
                "d'un dépôt distant. Utilise browse_repo d'abord pour trouver le bon chemin."
            ),
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
            "description": (
                "Liste les dépôts GitHub déjà indexés dans LibraryBrain. "
                "Appelle cet outil pour savoir quels dépôts sont disponibles dans la base de connaissances."
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
            "name": "index_github_repo",
            "description": (
                "Indexe un dépôt GitHub dans LibraryBrain (README + docs) pour pouvoir "
                "l'interroger ensuite avec search_books. Utilise cet outil quand l'utilisateur "
                "veut ajouter un nouveau dépôt à sa base de connaissances."
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
                "Analyse un dépôt GitHub pour en extraire les bonnes pratiques : "
                "structure, outils, CI/CD, linting, dépendances. "
                "Retourne un rapport structuré que tu peux utiliser avec save_skill "
                "pour mémoriser les patterns utiles. "
                "Combine cet outil avec save_skill pour apprendre d'un dépôt."
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
            "description": (
                "Clone un dépôt GitHub dans le dossier projets et l'ouvre dans PyCharm. "
                "Utilise cet outil quand l'utilisateur veut récupérer un dépôt pour le travailler localement."
            ),
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
                "Crée un nouveau projet local à partir d'un template (python, fastapi, cli, empty) "
                "et l'ouvre dans PyCharm. Si 'inspired_by' est fourni, le LLM utilisera "
                "les bonnes pratiques de ce dépôt pour structurer le projet. "
                "Utilise extract_best_practices d'abord pour analyser le dépôt source, "
                "puis create_project pour créer la structure, puis adapte les fichiers "
                "avec write_file en s'inspirant du code source lu avec read_github_file."
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
                "Génère un aperçu local d'un code HTML/CSS/JS, démarre un serveur HTTP "
                "local et ouvre automatiquement le navigateur. "
                "Utilise cet outil après avoir généré du code web pour le prévisualiser. "
                "IMPORTANT : si ton code utilise une librairie externe (Three.js, Chart.js, "
                "d3, p5, etc.), déclare son URL CDN dans 'scripts' — sinon la page sera vide. "
                "La valeur de retour peut contenir des avertissements : lis-les et corrige."
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
                "Ouvre un fichier HTML existant du projet dans le navigateur via le serveur de prévisualisation. "
                "Le fichier est copié dans le dossier de prévisualisation."
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
                "Génère un classeur Excel (.xlsx) téléchargeable par l'utilisateur. "
                "Utilise cet outil dès qu'on te demande un fichier Excel, un tableur, "
                "un export en .xlsx ou « télécharge-moi un Excel ». Fournis les données "
                "structurées en feuilles (onglets) : chaque feuille a des en-têtes de "
                "colonnes et des lignes. NE produis PAS le binaire toi-même et n'écris "
                "pas de code Python — passe les données, l'outil construit le fichier et "
                "renvoie une URL de téléchargement (un bouton apparaît dans l'UI)."
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
                "Génère un fichier TEXTE ou CODE téléchargeable par l'utilisateur "
                "(.txt, .md, .csv, .json, .xml, .html, .css, .js, .ts, .tsx, .jsx, "
                ".py, .php, .sql, .yaml, .rtf…). Utilise cet outil dès qu'on te demande "
                "de « générer » ou « télécharger » un fichier de ce type. Passe le "
                "CONTENU complet en texte — n'écris pas de code pour le construire. "
                "L'extension est déduite du nom (ramenée à .txt si inconnue). Renvoie une "
                "URL de téléchargement (un bouton apparaît dans l'UI). Pour PLUSIEURS "
                "fichiers d'un coup (mini-projet, dossier), utilise plutôt bundle_zip."
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
                "Regroupe plusieurs fichiers texte/code dans une archive .zip "
                "téléchargeable. Idéal pour livrer un mini-projet ou un dossier complet "
                "(ex: une app React / Next.js / Symfony) en un seul téléchargement. "
                "Fournis la liste des fichiers avec leur chemin relatif et leur contenu ; "
                "les sous-dossiers sont conservés (ex: 'src/App.tsx'). Renvoie une URL de "
                "téléchargement (un bouton apparaît dans l'UI)."
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
            "description": (
                "Dit un court texte À VOIX HAUTE sur les haut-parleurs du Mac avec la "
                "voix de Klody (TTS local VocalBrain, quelques secondes). Utilise cet "
                "outil quand l'utilisateur demande de parler, dire, lire ou annoncer "
                "quelque chose à voix haute, ou pour signaler oralement la fin d'une "
                "longue tâche. Texte court (≤ 600 caractères). Ce n'est PAS pour "
                "générer des chansons (→ mcp__vocalbrain__generer_chanson)."
            ),
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
                "Regarde une IMAGE locale et répond à une question dessus avec le "
                "modèle vision de Klody (VL local). Utilise cet outil DÈS qu'il faut "
                "VOIR le contenu d'une image : capture d'écran, photo, schéma, "
                "diagramme, graphique, maquette UI, document scanné, OCR. Le cerveau "
                "ne voit PAS les images lui-même — passe TOUJOURS par cet outil pour "
                "tout ce qui est visuel. Formats : png, jpg, jpeg, webp, gif, bmp."
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

TOOLS = [*TOOLS, LIST_SKILLS_TOOL, DELETE_SKILL_TOOL, SKILL_TOOL, *IMPORT_TOOLS, *MCP_TOOLS, *MEMORY_TOOLS, *GITHUB_TOOLS, *PROJECT_TOOLS, *PREVIEW_TOOLS, *AUDIO_TOOLS, *DOCUMENT_TOOLS, *VOICE_TOOLS, *IMAGE_TOOLS]


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

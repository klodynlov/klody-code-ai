"""Gardes-fous de l'orchestrateur : doc, LibraryBrain, anti-stall, anti-boucle.

Extraits de ``agent/orchestrator.py`` (lot 4.1a, zéro changement de comportement).
Les symboles publics sont ré-exportés par ``agent.orchestrator`` — les tests qui
font ``from agent.orchestrator import X`` continuent de fonctionner.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePosixPath

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

# ── Garde LibraryBrain : absence affirmée sans avoir interrogé le CONTENU ──────
#
# `library_catalog` n'indexe que titre+auteur : un miss sur une requête THÉMATIQUE
# (« bébé », « puériculture ») ne prouve rien sur ce que contiennent les livres.
# Incident 27/07 : 5 `library_catalog` à vide, ZÉRO `search_books`, puis « il n'y
# a pas de sources dans LibraryBrain » — faux. `prompts/explain.md` l'interdit
# déjà, mais une consigne de prompt perd contre une sortie d'outil concrète : d'où
# ce filet en dur. Miroir côté agent de `_augment_no_hit`/`_catalog_miss` (tools).
_NO_SOURCE_CLAIM_RE = re.compile(
    r"aucun\w*\s+(?:\w+\s+){0,2}?(?:source|livre|ouvrage|r[ée]sultat|info\w*|"
    r"document|r[ée]f[ée]rence)"
    r"|(?:pas|plus)\s+(?:de\s+|d')(?:source|livre|ouvrage|info\w*|r[ée]f[ée]rence|"
    r"document|trace)"
    r"|(?:rien|pas)\s+(?:de\s+(?:pertinent|dispo\w*)\s+)?(?:dans|sur|parmi)\s+"
    r"(?:la|ta|ma|votre|notre|les|tes|mes)?\s*(?:biblioth[eè]que|livres|"
    r"librarybrain|catalogue|base)"
    r"|(?:biblioth[eè]que|librarybrain|catalogue)\s+(?:\w+\s+){0,2}?"
    r"(?:est\s+vide|ne\s+(?:contient|comporte|couvre)|n'a\s+rien)"
    r"|(?:pas|non)\s+index[ée]"
    r"|n'ai\s+(?:rien|pas)\s+trouv[ée]"
)


def _claims_no_library_source(content: str | None) -> bool:
    """Vrai si la réponse affirme qu'il n'y a ni source ni livre sur le sujet.

    Pur → testable sans LLM. Normalise l'apostrophe typographique (le modèle
    alterne « n'ai » et « n'ai ») avant de matcher `_NO_SOURCE_CLAIM_RE`.
    """
    if not content:
        return False
    return bool(_NO_SOURCE_CLAIM_RE.search(content.lower().replace("’", "'")))


# ── Anti-stall : plan annoncé sans action ──────────────────────────────────────

def _looks_like_unfinished_plan(content: str | None) -> bool:
    """Détecte un message qui annonce un plan ou des intentions sans agir.

    Patterns courants observés sur Qwen3-Coder en mode hard/feature avec T basse :
    - "Voici mon plan : 1. … 2. … Commençons par X :"
    - "Je vais créer Y. Tout d'abord :"
    - "Je vais X. Je vais Y. Je vais d'abord Z." (≥2 intentions sans action)
    - Finir par ":" / "…" / "..." après une énumération
    """
    if not content:
        return False
    stripped = content.strip()
    if len(stripped) < 30:
        return False
    lower = stripped.lower()

    # 1) Finit par marqueur d'incomplétude
    ends_open = stripped[-1:] in (":", "…") or stripped.endswith("...")

    # 2) Phrases d'intention future (le LLM annonce ce qu'il VA faire)
    intent_patterns = (
        "je vais", "i will", "i'll ", "let's", "commençons par",
        "tout d'abord", "first,", "step 1", "étape 1",
        "je commence", "je vais d'abord", "je vais ensuite",
        "je vais maintenant", "voici mon plan", "let me start",
    )
    intent_count = sum(lower.count(p) for p in intent_patterns)

    # 3) Énumération markdown ou liste indentée
    has_enumeration = (
        "\n1." in content or "\n1)" in content or
        content.lstrip().startswith("1.") or
        content.count("\n    ") >= 2 or       # liste indentée 4 espaces
        content.count("\n- ") >= 2            # liste à tirets
    )

    # Triggers (en OR — il suffit qu'un seul soit vrai pour déclencher) :
    # - Finit ouvert avec ≥1 intention OU énumération
    # - OU ≥2 intentions futures distinctes (pattern "Je vais X. Je vais Y.")
    # - OU énumération + ≥1 intention
    if ends_open and (intent_count >= 1 or has_enumeration):
        return True
    if intent_count >= 2:
        return True
    return bool(has_enumeration and intent_count >= 1)


def _is_empty_after_reasoning(
    content: str | None,
    has_tool_calls: bool,
    *,
    thinking_enabled: bool,
    use_bon: bool,
    already_recovered: bool,
) -> bool:
    """Vrai si le tour n'a produit NI réponse NI action alors que le CoT était actif.

    Sous-cas DISTINCT de la boucle-verbatim (que `LoopGuard`/`degenerate_cut` coupe
    déjà dans le stream) : ici le raisonnement a consommé tout le budget de tokens
    SANS jamais répéter à l'identique (analysis-paralysis — « vérifions X, puis Y,
    puis Z »), donc `degenerate_cut` ne matche pas, et pourtant `content` ressort
    VIDE. Sur une tâche `explain`/`chat`, l'anti-stall (qui ne couvre que
    feature/refactor/self_dev/bug_fix) laisse alors un écran blanc.

    Pur → testable sans LLM. Exclut Best-of-N (candidats vides = filet propre) et se
    limite à un seul déclenchement par run (`already_recovered`).
    """
    return (
        thinking_enabled
        and not use_bon
        and not already_recovered
        and not has_tool_calls
        and not (content or "").strip()
    )


# ── Anti-boucle cross-run : commande shell en échec ───────────────────────────

def _cmd_result_failed(result: str) -> bool:
    """Vrai si le résultat d'une commande shell dénote un ÉCHEC.

    `[Code de retour: N]` n'est ajouté par le terminal QUE si returncode≠0 ;
    `ERREUR…` / `ERREUR SÉCURITÉ…` couvrent exception, timeout et blocage
    sécurité. Sert à ne compter que les échecs cross-run (une commande qui
    réussit — ou dont la sortie évolue — ne doit jamais faire monter le compteur).
    """
    low = (result or "").lower()
    return "[code de retour:" in low or low.startswith("erreur")


# ── Garde « les décisions du projet n'ont jamais été ouvertes » ───────────────
# Mesuré le 2026-07-30 sur le palier `discovery` du banc (cf.
# bench/results/reference_2026-07-30_lot_trace_ouverture_docs.json) : quand
# l'agent ouvre un document du projet, il tient la contrainte non écrite **8 fois
# sur 8** ; quand il ne l'ouvre pas, **0 fois sur 10**. Séparation parfaite,
# n=18, Fisher p = 2,3e-05.
#
# Ce qui l'arrête n'est PAS le budget : c'est un VERT. Il écrit le fichier, lance
# la suite de tests déjà présente, la voit passer, et conclut. Or ces tests ont
# été écrits AVANT sa modification : ils ne peuvent pas couvrir une contrainte
# qu'il n'a jamais lue. Le signal qui le fait s'arrêter ne porte pas la
# conclusion qu'il en tire — même sophisme que le garde LibraryBrain, où un
# catalogue qui n'indexe que les titres ne prouve rien sur le contenu.
#
# On attaque donc le CRITÈRE D'ARRÊT, pas la lecture : l'instruction (« explore
# avant d'écrire ») est déjà dans l'énoncé du banc, et une consigne de plus avait
# échoué (0/3, annulée). Une instruction ne peut pas concurrencer un test vert.
_DOC_SUFFIXES = frozenset({".md", ".rst", ".adoc"})
# Dossiers où un projet range ses décisions. `docs` est une convention répandue,
# pas une particularité du banc — la même règle attrape doc/, adr/, rfcs/.
_DOC_DIRS = frozenset({"docs", "doc", "adr", "decisions", "rfc", "rfcs"})
# Balayage borné : un README à la racine et docs/**/*.md sont couverts sans
# descendre un arbre entier. Le garde tourne au moment de conclure, pas à chaque
# tour, mais un `rglob` sur un gros dépôt se paierait quand même.
_DOC_SCAN_DEPTH = 3
_DOC_SCAN_MAX = 40
# Cités dans le nudge. Nommer les documents ne donne pas la réponse — elle est
# DANS le document, qu'il faut encore lire et appliquer. C'est l'exact pendant du
# garde LibraryBrain, qui nomme `search_books` sans répondre à la question.
_DOC_NUDGE_MAX = 6
# Outils qui modifient le dépôt. Volontairement plus étroit que _PRODUCING_TOOLS :
# un `preview_code` ou un `run_in_sandbox` fabrique un artefact jetable, il
# n'engage pas le projet et ne justifie pas d'exiger la lecture de ses décisions.
_DOC_WRITE_TOOLS = frozenset({"write_file", "create_project", "scaffold_tool"})


def _est_documentation(path: str) -> bool:
    """Vrai si `path` désigne un document de projet (décisions, conventions).

    Deux formes : une extension de documentation à la racine (README.md,
    CONTRIBUTING.md), ou n'importe quel fichier sous un dossier de documentation.
    Un `.md` quelque part dans du code compte aussi — le garde préfère se
    désarmer à tort que forcer une lecture inutile.
    """
    p = PurePosixPath(str(path).replace("\\", "/"))
    if p.suffix.lower() in _DOC_SUFFIXES:
        return True
    return any(part.lower() in _DOC_DIRS for part in p.parts[:-1])


# ── Anti-boucle per-run ───────────────────────────────────────────────────────
# Le LLM peut rappeler le MÊME outil avec les MÊMES arguments en rafale quand le
# résultat ne le satisfait pas (typiquement un `run_in_sandbox` qui plante à
# l'identique — proxy mort, script cassé). Sans garde-fou il tourne jusqu'à
# épuiser max_iter (avec auto-continue, jusqu'à 30 passes) sans rien produire :
# c'est la « boucle » visible côté utilisateur. On compte les appels (nom + args)
# identiques sur le run ; au 3e on injecte un avertissement (change d'approche),
# au 4e on coupe et on force la synthèse finale.
_LOOP_REPEAT_WARN = 3
_LOOP_REPEAT_BREAK = 4

# Anti-scan : variante « lecture errante ». L'anti-boucle ci-dessus clé sur
# nom+args+résultat — elle ne voit PAS un modèle qui balaie 40 fichiers DIFFÉRENTS
# au hasard (40 read_file = 40 clés = compteur jamais > 1) en cherchant une info
# qu'il ne localise pas. Symptôme observé : storm de read_file à la racine, cap
# d'itérations atteint, réponse vide. On compte ici le MÊME OUTIL (quels que
# soient args/résultat), restreint aux outils d'exploration (non producteurs) :
# au seuil WARN on pousse à utiliser list_files/find_relevant_files ; au BREAK on
# coupe et on synthétise. Seuils plus hauts que l'anti-boucle : explorer un repo
# légitimement peut demander plusieurs lectures.
_SCAN_REPEAT_WARN = 8
_SCAN_REPEAT_BREAK = 14
# Outils d'exploration de FICHIERS : quand l'anti-scan se déclenche sur l'un d'eux,
# la remédiation « cadre le dossier avec list_files / localise par contenu » a un
# sens. Pour tout AUTRE outil pris en rafale (ex. un outil MCP), ce message oriente
# à tort vers la lecture de fichiers — on bascule alors sur une consigne générique.
_FILE_SCAN_TOOLS = frozenset({
    "read_file", "search_in_files", "find_relevant_files", "find_references",
    "find_symbol", "list_files", "preview_file",
})

# Anti-écho : un outil PRODUCTEUR réémis avec EXACTEMENT les mêmes arguments
# refabrique le même artefact — jamais un progrès — mais son résultat peut
# différer en surface (preview_code écrit preview-24, -25, -26… → URL neuve →
# hash(résultat) différent → l'anti-boucle nom+args+résultat ne monte jamais).
# Vécu 03/07 (« canard 3D ») : 11 preview_code identiques avec js vide (émission
# XML du tool call cassée), chaque appel « réussissait », 25 itérations brûlées
# puis dérive totale de l'agent. On compte la série CONSÉCUTIVE du même appel
# producteur (nom+args, résultat IGNORÉ) : la série casse dès qu'un AUTRE appel
# producteur passe (write_file puis re-preview du même fichier = workflow
# légitime) ; les lectures/sondages intercalés ne la cassent pas (ils ne
# changent pas l'appel réémis). Au WARN on signale l'écho au modèle (un argument
# est probablement vide/tronqué) ; au BREAK on coupe et on force la synthèse.
_ECHO_REPEAT_WARN = 3
_ECHO_REPEAT_BREAK = 5

# Anti-boucle COMPORTEMENTALE cross-run : une commande shell qui échoue à
# l'identique d'un run() à l'autre. Angle mort réel (08/07) : `call_repeat_counts`
# est reset à CHAQUE message (l'orchestrator est reconstruit par message WS) ET le
# text-to-action fallback exécute SANS l'alimenter → une commande qui rate pareil
# à chaque tour (ex. `python main.py` lancé depuis la mauvaise racine → « No such
# file ») passait sous tous les radars et rebouclait sans fin. On porte le
# compteur sur la MÉMOIRE de session (persistante entre messages) et on coupe dès
# le 2e échec identique consécutif (+ nudge correctif persistant). Seuil bas : un
# échec qui se répète à l'identique n'a AUCUNE raison de converger tout seul.
_CMD_FAIL_STREAK_BREAK = 2
# Outils dont l'exécution lance une commande shell (les seuls suivis cross-run).
_CMD_EXEC_TOOLS = frozenset({"execute_command"})


# ── Mixin : méthodes de garde portées sur l'Orchestrator ──────────────────────

class GardesMixin:
    """Méthodes de garde de l'orchestrateur (doc, LibraryBrain, anti-stall,
    anti-boucle cross-run). Injecté dans ``Orchestrator`` par héritage."""

    def _note_library_probe(self, tool_name: str, result: str) -> None:
        """Suit l'usage des deux ponts LibraryBrain sur le run courant.

        Seul un hit EXACT (« N livre(s) au catalogue pour … ») compte comme une
        trouvaille catalogue. Un miss ET une correspondance PARTIELLE laissent la
        question du CONTENU entière → ils arment le garde-fou. `search_books`
        compte dès qu'il est APPELÉ, même en erreur : le garde vise « jamais
        essayé », pas « essayé sans succès » (sinon il relancerait sans fin sur un
        LibraryBrain hors-ligne).
        """
        if tool_name == "library_catalog":
            if not re.match(r"\d+ livre\(s\) au catalogue pour ", result):
                self._catalog_missed = True
        elif tool_name == "search_books":
            self._content_searched = True

    def _should_force_content_search(
        self, content: str | None, iteration: int, max_iter: int
    ) -> bool:
        """Vrai si la réponse conclut à l'absence sans avoir interrogé le CONTENU.

        Incident 27/07 : 5 `library_catalog` sur « bébé / puériculture / parenting »,
        ZÉRO `search_books`, puis « il n'y a pas de sources dans LibraryBrain » —
        faux, la bibliothèque avait la matière. Le catalogue n'indexe que
        titre+auteur : il ne peut RIEN conclure sur le contenu.

        Une seule relance par run (`_library_guard_fired`) : si `search_books` ne
        rend rien non plus, la 2e conclusion d'absence passe. Il faut aussi une
        itération de marge, sinon la relance meurt sur le cap sans réponse.
        """
        return (
            self._catalog_missed
            and not self._content_searched
            and not self._library_guard_fired
            and iteration < max_iter - 1
            and _claims_no_library_source(content)
        )

    def _note_doc_probe(self, tool_name: str, tool_args: dict, result: str) -> None:
        """Suit, sur le run courant, ce que l'agent a lu et ce qu'il a écrit.

        Une lecture compte quand elle porte sur un document (`read_file`), et
        aussi quand une recherche RAMÈNE du contenu de document : `search_in_files`
        affiche les chemins trouvés, donc l'agent a bien vu les décisions passer.
        Ne compter que `read_file` armerait le garde après une découverte par grep
        parfaitement valable.

        `list_files` ne compte PAS : voir un nom de fichier n'est pas lire ce qu'il
        contient. C'est exactement l'erreur du garde LibraryBrain — un catalogue de
        titres ne dit rien du contenu.
        """
        if tool_name in _DOC_WRITE_TOOLS:
            self._code_ecrit = True
            # Un document que l'agent vient d'ÉCRIRE n'est pas une décision du
            # projet — lui demander de le relire est absurde. Faux positif RÉEL,
            # attrapé par deux scénarios de rejeu (04, 12) dont la tâche était
            # « crée un README.md » : le garde exigeait la lecture du fichier
            # produit à l'instant, et brûlait un tour à chaque fois.
            rel = self._chemin_relatif(str((tool_args or {}).get("path", "")))
            if rel:
                self._doc_ecrits.add(rel)
            return
        if self._doc_consulte:
            return
        if tool_name == "read_file":
            chemin = (tool_args or {}).get("path", "")
            if chemin and _est_documentation(str(chemin)):
                self._doc_consulte = True
        elif tool_name == "search_in_files":
            if any(_est_documentation(ligne) for ligne in str(result).splitlines()[:200]):
                self._doc_consulte = True

    def _chemin_relatif(self, brut: str) -> str | None:
        """Chemin d'outil → chemin relatif POSIX à la racine, ou None hors racine.

        Les arguments d'outils arrivent tantôt relatifs (`README.md`), tantôt
        absolus ; l'inventaire, lui, ne rend que du relatif. Sans normalisation
        commune, l'exclusion des documents écrits par l'agent ne mordrait que sur
        l'une des deux formes.
        """
        if not brut:
            return None
        try:
            racine = Path(self.file_manager.root).resolve()
            chemin = Path(brut)
            chemin = chemin if chemin.is_absolute() else racine / chemin
            return chemin.resolve().relative_to(racine).as_posix()
        except (OSError, ValueError):
            return None

    def _documentation_du_projet(self) -> list[str]:
        """Documents du projet dans la racine courante, en chemins relatifs.

        Calculé au plus une fois par run et SEULEMENT quand le reste du garde est
        déjà vrai : un balayage systématique se paierait à chaque tour pour un
        garde qui ne sert qu'au moment de conclure. Un dépôt sans documentation
        rend une liste vide et le garde ne se déclenche jamais — c'est le cas des
        25 tâches non-`discovery` du banc, qui ne peuvent donc pas régresser.

        Les documents écrits par l'agent pendant ce run sont exclus (cf.
        `_note_doc_probe`). Le cache est donc posé au moment de conclure, quand
        cette liste d'exclusion est complète.
        """
        cache = getattr(self, "_doc_inventaire", None)
        if cache is not None:
            return cache
        trouves: list[str] = []
        try:
            racine = Path(self.file_manager.root).resolve()
            # ⚠️ Parcours ÉLAGUÉ, pas un `rglob("*")`. Mesuré sur ce dépôt :
            # `sorted(rglob("*"))` énumère 88 706 entrées en 0,79 s, parce qu'il
            # DESCEND dans `.venv`, `_downloads`, `graphify-out`… avant que le
            # filtre de profondeur ne les écarte. On coupe les branches à
            # l'entrée : le coût devient celui des dossiers réellement utiles.
            for dossier, sous_dossiers, fichiers in os.walk(racine):
                rel_dir = Path(dossier).relative_to(racine)
                profondeur = 0 if rel_dir == Path(".") else len(rel_dir.parts)
                if profondeur + 1 >= _DOC_SCAN_DEPTH:
                    sous_dossiers[:] = []
                else:
                    sous_dossiers[:] = [
                        d for d in sous_dossiers if not d.startswith((".", "_"))
                    ]
                for nom in fichiers:
                    if nom.startswith((".", "_")):
                        continue
                    if Path(nom).suffix.lower() not in _DOC_SUFFIXES:
                        continue
                    rel = (rel_dir / nom).as_posix().removeprefix("./")
                    if rel not in self._doc_ecrits:
                        trouves.append(rel)
                if len(trouves) >= _DOC_SCAN_MAX:
                    break
        except OSError as exc:  # racine illisible → garde silencieux, jamais fatal
            logger.debug("[doc-guard] inventaire impossible : %s", exc)
            trouves = []
        self._doc_inventaire = sorted(trouves)[:_DOC_SCAN_MAX]
        return self._doc_inventaire

    def _should_force_doc_read(
        self, content: str | None, iteration: int, max_iter: int
    ) -> bool:
        """Vrai si l'agent conclut une modification du dépôt sans avoir ouvert un
        seul document du projet.

        ⚠️ Aucune analyse de `content`, à la différence du garde LibraryBrain — et
        c'est délibéré. Là-bas, la faute est dans ce que le modèle AFFIRME (une
        absence) ; une réponse sourcée est irréprochable avec les mêmes outils
        appelés. Ici la faute est dans ce qu'il n'a PAS fait : quelle que soit la
        formulation, une modification livrée sans avoir lu les décisions du projet
        repose sur un vert qui ne les couvre pas. Le paramètre est conservé pour
        garder la signature des gardes homogène et rester gréable plus tard.

        Une seule relance par run (`_doc_guard_fired`) : si l'agent lit le document
        et conclut quand même de travers, la 2e conclusion passe — le garde force
        une consultation, il ne juge pas le code. Marge d'une itération, sinon la
        relance meurt sur le cap sans réponse.
        """
        return (
            self._code_ecrit
            and not self._doc_consulte
            and not self._doc_guard_fired
            and iteration < max_iter - 1
            and bool(self._documentation_du_projet())
        )

    def _doc_guard_nudge(self, documents: list[str]) -> str:
        """Message injecté quand le garde se déclenche. Nomme les documents, comme
        le garde LibraryBrain nomme `search_books` : trouver le fichier n'est pas
        la difficulté mesurée — l'ouvrir l'est."""
        liste = ", ".join(f"`{d}`" for d in documents[:_DOC_NUDGE_MAX])
        reste = len(documents) - _DOC_NUDGE_MAX
        if reste > 0:
            liste += f" (+{reste} autre(s))"
        return (
            "STOP — ne conclus pas encore. Tu as modifié le projet sans avoir "
            "ouvert un seul de ses documents. Si tu t'appuies sur des tests qui "
            "passent : ils ont été écrits AVANT ta modification, donc ils ne "
            "testent pas ce que tu viens d'ajouter. Un vert obtenu sur eux ne "
            "prouve rien sur les règles du projet — il prouve seulement que tu "
            "n'as rien cassé de ce qui existait déjà.\n"
            f"Ce projet documente ses décisions ici : {liste}.\n"
            "Lis-les MAINTENANT avec `read_file`. Puis, une par une, confronte-les "
            "à ton code : si l'une impose quelque chose que tu n'as pas fait, "
            "corrige le code et relance les tests. Si après lecture tout est déjà "
            "conforme, dis-le en citant la règle vérifiée — et conclus."
        )

    def _cmd_loop_nudge(self, tool_args: dict, streak: int) -> None:
        """Signale la boucle de commande à l'utilisateur et injecte un nudge
        correctif PERSISTANT (vu au prochain run via la mémoire de session)."""
        cmd = str(tool_args.get("command", ""))[:200] if isinstance(tool_args, dict) else ""
        cwd = getattr(self.terminal, "cwd", "?")
        logger.warning(
            "[cmd-loop] commande échouée %d× à l'identique (cross-run) → nudge + stop",
            streak)
        console.print(
            f"\n[yellow]  ⚠  Commande en échec répété ({streak}×) — arrêt, "
            f"correction demandée au modèle.[/yellow]"
        )
        self.memory.messages.append({
            "role": "user",
            "content": (
                f"STOP. La commande `{cmd}` a ÉCHOUÉ {streak} fois DE SUITE à "
                f"l'identique (lancée depuis « {cwd} »). NE la relance PAS telle "
                "quelle — ce serait une boucle. Diagnostique d'ABORD la cause : le "
                "fichier/chemin existe-t-il à cet endroit ? une dépendance manque-"
                "t-elle ? Puis corrige : chemin ABSOLU, `cd <bon_dossier> && …`, ou "
                "crée le fichier avec write_file avant de l'exécuter. Si tu ne peux "
                "pas corriger, explique le blocage à l'utilisateur en clair. Rédige "
                "maintenant ta réponse — n'appelle plus cette commande à l'identique."
            ),
            "timestamp": None,
        })

#!/usr/bin/env python3
"""Klody Code Ai — Interface CLI next-level."""

import argparse
import logging
import re
import sys
import threading
from pathlib import Path

from agent.greeting import AccueilEnTacheDeFond
from agent.long_term_memory import get_long_term_memory
from agent.memory import ConversationMemory
from agent.memory_extractor import extract_and_save
from agent.orchestrator import Orchestrator
from config import (
    BACKEND,
    GREETING_VOICE,
    LIBRARYBRAIN_DIR,
    LIBRARYBRAIN_URL,
    LLM_BASE_URL,
    LLM_MODEL,
    MEMORY_DIR,
    OLLAMA_BASE_URL,
    PREVIEW_DIR,
    PREVIEW_PORT,
    PROJECT_ROOT,
    SEMANTIC_MEMORY_PROVIDER,
    VOICE_REPLIES,
)
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from services import PROBE_UNAUTHORIZED, ensure_librarybrain, get_librarybrain_status

logger = logging.getLogger(__name__)
console = Console()

# ------------------------------------------------------------------ #
# Styles prompt_toolkit                                               #
# ------------------------------------------------------------------ #

_PT_STYLE = Style.from_dict({
    "prompt":        "bold ansibrightgreen",
    "bottom-toolbar": "bg:#1a1a2e #6272a4",
    "auto-suggestion": "#555555",
})

_HISTORY_FILE = Path.home() / ".klody_history"

# Lecture vocale des réponses (accessibilité). Basculable en session par /voix ;
# valeur de départ héritée de la config (GREETING_VOICE → VOICE_REPLIES).
_voix_reponses: bool = VOICE_REPLIES

# Un bloc de code lu à voix haute est du bruit pur — trente secondes de
# ponctuation épelée. On le remplace par une mention, l'écran l'a de toute façon.
_BLOC_CODE = re.compile(r"```.*?(```|$)", re.DOTALL)
_MARQUES_MD = re.compile(r"[*_`#>|]+")

# ------------------------------------------------------------------ #
# Bannière                                                             #
# ------------------------------------------------------------------ #

def _backend_label() -> str:
    """Nom lisible du backend LLM réellement visé, dérivé de BACKEND.

    Lit le global du module (et non `config.BACKEND` capturé) pour rester
    surchargeable en test sans recharger config — un `importlib.reload` recrée
    les classes du module et casse les `pytest.raises` alentour.
    """
    return "MLX" if BACKEND == "mlx" else "Ollama"


def _sonde_backend_llm() -> tuple[str, str, str]:
    """Sonde le backend LLM RÉELLEMENT actif. Retourne (service, état, détails).

    ⚠️ C'est une sonde de DISPONIBILITÉ, délibérément pas de résolution d'alias :
    on interroge `/v1/models`, jamais une complétion. Une complétion peut
    déclencher le chargement des 44 Go de `brain` ou se faire refuser en 503 —
    c'est le piège du préflight du nightly, qui FABRIQUAIT la condition qu'il
    prétendait détecter (`vm_stat` sous-estime la RAM juste après un gros
    chargement : 41 GiB à t+9 s, 62 GiB à t+2 min, rien n'ayant été libéré).
    Un /status ne doit rien coûter à la machine.

    Corollaire : le CONTENU de `/v1/models` ne veut rien dire en mode autonome
    (`mlx_lm.server` y liste tout le cache HF, pas le modèle chargé). Seul le
    fait qu'il réponde est exploitable — d'où un détail qui montre l'URL sondée
    plutôt qu'un décompte trompeur.
    """
    import httpx

    libelle = f"Backend LLM ({_backend_label()})"
    if BACKEND == "mlx":
        url = f"{LLM_BASE_URL.rstrip('/')}/models"
        remede = "démarrer le gateway Klody Core (:8090) ou start-local-ai.sh"
    else:
        url = OLLAMA_BASE_URL.replace("/v1", "") + "/api/tags"
        remede = "ollama serve"

    try:
        reponse = httpx.get(url, timeout=2.0)
        reponse.raise_for_status()
    except Exception:
        return libelle, "[red]✗ hors ligne[/red]", remede

    if BACKEND == "mlx":
        return libelle, "[green]✓ en ligne[/green]", LLM_BASE_URL
    try:
        n = len(reponse.json().get("models", []))
    except Exception:
        return libelle, "[green]✓ en ligne[/green]", OLLAMA_BASE_URL
    return libelle, "[green]✓ en ligne[/green]", f"{n} modèle(s)"


def _ligne_embeddings() -> tuple[str, str, str]:
    """Ligne « Embeddings » — sondée seulement si Ollama les sert vraiment.

    /status sondait Ollama INCONDITIONNELLEMENT et affichait « ✗ hors ligne »
    en mode mlx, où Ollama n'est ni le backend LLM ni le fournisseur
    d'embeddings (`SEMANTIC_MEMORY_PROVIDER` vaut `st` par défaut :
    sentence-transformers en processus, `cos(ollama, st) = 1.0000` mesuré).

    Un avertissement qui décrit un problème inexistant coûte autant qu'une
    erreur fausse : le 2026-07-30, un faux « Ollama injoignable » en tête de log
    a orienté tout le diagnostic d'un 1/5 vers Ollama, alors que la cause était
    `MLX_CODE_BASE_URL`.

    Hors mode ollama, on n'invente donc pas un verdict : on affiche « non
    sondé » et le fournisseur configuré. Vérifier réellement `st` imposerait de
    charger bge-m3 en processus — un /status ne doit rien coûter.
    """
    if SEMANTIC_MEMORY_PROVIDER != "ollama":
        return (
            "Embeddings",
            "[dim]non sondé[/dim]",
            f"fournisseur « {SEMANTIC_MEMORY_PROVIDER} », en processus",
        )

    import httpx

    try:
        reponse = httpx.get(OLLAMA_BASE_URL.replace("/v1", "") + "/api/tags", timeout=2.0)
        reponse.raise_for_status()
    except Exception:
        return "Embeddings", "[red]✗ Ollama hors ligne[/red]", "ollama serve"
    return "Embeddings", "[green]✓ Ollama en ligne[/green]", "fournisseur « ollama »"


def print_banner(memory: ConversationMemory) -> None:
    console.print()

    title = Text()
    title.append("  ◆  ", style="bold blue")
    title.append("KLODY", style="bold white")
    title.append(" CODE AI", style="bold cyan")
    title.append("  ◆", style="bold blue")

    # Le sous-titre nommait « Ollama » EN DUR, quel que soit BACKEND. C'est le
    # défaut du dépôt qui coûte le plus cher : un en-tête qui nomme la mauvaise
    # dépendance envoie tout le diagnostic au mauvais endroit (vécu le
    # 2026-07-30 : le nightly rend 1/5 avec « Impossible de joindre Ollama »
    # alors qu'en BACKEND=mlx l'appel va au gateway ; Ollama n'est même pas
    # installé sur la machine, il ne sert que les embeddings — et encore, plus
    # depuis SEMANTIC_MEMORY_PROVIDER=st). On DÉRIVE désormais de la config au
    # lieu de l'affirmer.
    subtitle = Text(f"  Servi par {_backend_label()} · 100% local · privé  ", style="dim")

    console.print(Panel(
        Align.center(title + Text("\n") + subtitle),
        border_style="bold blue",
        padding=(0, 4),
        box=box.DOUBLE_EDGE,
    ))
    console.print()

    # Tableau d'état compact
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), expand=False)
    table.add_column(style="dim", no_wrap=True)
    table.add_column(style="bold")

    non_system = sum(1 for m in memory.messages if m["role"] != "system")
    # MODEL_NAME est le modèle du mode OLLAMA. En BACKEND=mlx, l'agent parle à
    # MLX_MODEL : la bannière annonçait donc « qwen3.5:9b » pendant que la
    # toolbar (`orchestrator.llm.model`, deux lignes plus bas dans l'écran)
    # affichait autre chose. LLM_MODEL est la valeur effectivement envoyée au
    # backend — en mode gateway c'est un ALIAS (`brain`, `coder`), et c'est bien
    # ce qu'on veut montrer : ce que Klody demande, pas ce qu'on suppose servi.
    table.add_row("⚙  Modèle",  f"[green]{LLM_MODEL}[/green]")
    table.add_row("📁 Projet",  f"[white]{PROJECT_ROOT}[/white]")
    table.add_row("🔑 Session", f"[cyan]{memory.session_id}[/cyan]")
    table.add_row("💬 Messages", str(non_system))

    console.print(Align.center(table))
    console.print()
    console.print(
        Align.center(Text("/help  /clear  /memory  /sessions  /model  /skills  /profile  /preview  /status  /exit", style="dim"))
    )
    console.print(Rule(style="dim blue"))
    console.print()


# ------------------------------------------------------------------ #
# Toolbar prompt_toolkit (bas de l'écran)                             #
# ------------------------------------------------------------------ #

def make_toolbar(orchestrator: Orchestrator):
    """Retourne une fonction qui génère la toolbar dynamique."""
    def get_toolbar():
        mem = orchestrator.memory
        n = sum(1 for m in mem.messages if m["role"] != "system")
        tokens = orchestrator.llm.total_tokens
        model = orchestrator.llm.model
        return HTML(
            f"  <b>◆ Klody</b>  │  "
            f"<ansicyan>{model}</ansicyan>  │  "
            f"session <b>{mem.session_id}</b>  │  "
            f"{n} messages  │  "
            f"~{tokens:,} tokens"
        )
    return get_toolbar


# ------------------------------------------------------------------ #
# Commandes spéciales                                                  #
# ------------------------------------------------------------------ #

HELP_TEXT = """
[bold]Commandes :[/bold]

  [cyan]/help[/cyan]              Cette aide
  [cyan]/clear[/cyan]             Effacer l'historique de la session
  [cyan]/memory[/cyan]            Session courante + souvenirs long terme
  [cyan]/model[/cyan]             Afficher le modèle actif
  [cyan]/model <nom>[/cyan]       Changer de modèle  [dim]ex: /model qwen3.5:9b[/dim]
  [cyan]/sessions[/cyan]          Lister et charger une session précédente
  [cyan]/skills[/cyan]            Lister les compétences mémorisées
  [cyan]/tokens[/cyan]            Afficher le compteur de tokens
  [cyan]/preview[/cyan]           Aperçus HTML disponibles + URLs
  [cyan]/export[/cyan]            Exporter la session en Markdown
  [cyan]/profile[/cyan]           Profil utilisateur détecté (techs, patterns)
  [cyan]/status[/cyan]            État du système (backend LLM, LibraryBrain, Preview)
  [cyan]/voix[/cyan]              Lire les réponses à voix haute (accessibilité)
  [cyan]/exit[/cyan]              Quitter

[bold]Apprentissage & Profil :[/bold]
  Klody profile l'utilisateur pour anticiper ses besoins et apprend
  de nouvelles connaissances via LibraryBrain.

  Exemples :
    [dim]« Apprends les design patterns Python depuis la bibliothèque »[/dim]
    [dim]« Quelles sont les bonnes pratiques React ? »[/dim]

[bold]Aperçu web :[/bold]
  Klody peut générer du code HTML/CSS/JS et l'ouvrir automatiquement
  dans le navigateur pour un aperçu local instantané.

  Exemples :
    [dim]« Crée une page d'accueil responsive avec Tailwind »[/dim]
    [dim]« Fais un jeu Snake en JS avec aperçu »[/dim]
    [dim]« Montre-moi un formulaire de login stylé »[/dim]

[bold]GitHub & Projets :[/bold]
  Parcourir un dépôt GitHub, en extraire les bonnes pratiques,
  cloner et ouvrir dans PyCharm, ou créer un nouveau projet.

  Exemples :
    [dim]« Montre-moi la structure de fastapi/fastapi »[/dim]
    [dim]« Analyse les bonnes pratiques de tiangolo/sqlmodel »[/dim]
    [dim]« Clone ce dépôt et ouvre-le dans PyCharm »[/dim]
    [dim]« Crée un projet FastAPI inspiré de ce dépôt »[/dim]

[dim]Saisie multi-ligne : terminer une ligne par  \\  puis Entrée[/dim]
[dim]Historique : flèches ↑ ↓[/dim]
"""


def handle_special_command(cmd: str, orchestrator: Orchestrator) -> bool:
    token = cmd.strip().lower()

    if token == "/help":
        console.print(Panel(HELP_TEXT, title="[bold]Klody Code Ai[/bold]", border_style="blue"))
        return True

    if token == "/clear":
        orchestrator.memory.clear()
        console.print("\n  [green]✓[/green] Historique effacé.\n")
        return True

    if token == "/voix":
        global _voix_reponses
        _voix_reponses = not _voix_reponses
        etat = "activée" if _voix_reponses else "désactivée"
        console.print(f"\n  [green]✓[/green] Lecture vocale des réponses {etat}.\n")
        # À l'ACTIVATION, on sonde tout de suite : découvrir une voix cassée au
        # bout de trois réponses muettes coûte bien plus qu'attendre ici. La
        # sonde est courte et son compte rendu est affiché quoi qu'il arrive.
        if _voix_reponses:
            try:
                from tools.voice import speak

                _signaler_voix(speak("Lecture vocale activée.", "fr"))
            except Exception as exc:
                _signaler_voix(f"speak inutilisable : {exc}")
        return True

    if token == "/memory":
        # Stats session courante
        stats = orchestrator.memory.stats()
        table = Table(title="Session courante", box=box.ROUNDED, border_style="cyan")
        table.add_column("Clé", style="dim", no_wrap=True)
        table.add_column("Valeur", style="bold")
        for k, v in stats.items():
            table.add_row(k, str(v))
        console.print(table)

        # Mémoire longue terme
        lt = get_long_term_memory()
        entries = lt.list_all()
        if entries:
            _CATEGORY_LABELS = {
                "user": "Utilisateur", "project": "Projets",
                "preference": "Préférences", "context": "Contexte",
            }
            lt_table = Table(
                title=f"[bold]{len(entries)} Souvenir(s) long terme[/bold]",
                box=box.ROUNDED, border_style="magenta", show_lines=True,
            )
            lt_table.add_column("Catégorie", style="dim", no_wrap=True)
            lt_table.add_column("Clé", style="bold cyan", no_wrap=True)
            lt_table.add_column("Contenu", style="white")
            for e in entries:
                lt_table.add_row(
                    _CATEGORY_LABELS.get(e["category"], e["category"]),
                    e["key"],
                    e["content"],
                )
            console.print()
            console.print(lt_table)
        else:
            console.print("\n  [dim]Aucun souvenir long terme. Klody mémorisera automatiquement les faits importants.[/dim]\n")
        return True

    if token == "/skills":
        from tools.skills import load_skills
        skills = load_skills()
        if not skills:
            console.print("\n  [dim]Aucune compétence mémorisée.[/dim]  [dim]Utilisez save_skill pour en créer.[/dim]\n")
        else:
            tbl = Table(
                title=f"[bold]{len(skills)} Compétence(s) mémorisée(s)[/bold]",
                box=box.ROUNDED,
                border_style="blue",
                show_lines=True,
            )
            tbl.add_column("Nom", style="bold white", no_wrap=True)
            tbl.add_column("Slug", style="dim cyan", no_wrap=True)
            tbl.add_column("Description", style="white")
            tbl.add_column("Mis à jour", style="dim", no_wrap=True)
            for s in skills:
                tbl.add_row(
                    s.get("name", "?"),
                    s.get("slug", "?"),
                    s.get("description", "")[:70],
                    s.get("updated", "")[:10],
                )
            console.print()
            console.print(tbl)
            console.print(
                "  [dim]Pour supprimer : demandez à Klody « supprime la compétence <slug> »[/dim]\n"
            )
        return True

    if token == "/tokens":
        t = orchestrator.llm.total_tokens
        console.print(f"\n  [cyan]~{t:,} tokens[/cyan] estimés cette session.\n")
        return True

    if token.startswith("/model"):
        parts = cmd.strip().split(maxsplit=1)
        if len(parts) == 2:
            new_model = parts[1].strip()
            orchestrator.llm.model = new_model
            console.print(f"\n  [green]✓[/green] Modèle → [bold]{new_model}[/bold]\n")
        else:
            console.print(f"\n  Modèle actif : [bold green]{orchestrator.llm.model}[/bold green]\n")
        return True

    if token == "/sessions":
        files = sorted(
            MEMORY_DIR.glob("memory_*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:15]
        if not files:
            console.print("\n  [dim]Aucune session sauvegardée.[/dim]\n")
            return True

        import json as _json
        tbl = Table(
            title="[bold]Sessions récentes[/bold]",
            box=box.ROUNDED, border_style="cyan", show_lines=False,
        )
        tbl.add_column("#", style="dim", width=3, no_wrap=True)
        tbl.add_column("ID", style="bold cyan", no_wrap=True)
        tbl.add_column("Titre", style="white")
        tbl.add_column("Msgs", style="dim", no_wrap=True)
        tbl.add_column("Modifié", style="dim", no_wrap=True)

        session_ids = []
        for i, f in enumerate(files, 1):
            try:
                data = _json.loads(f.read_text())
                sid = data.get("session_id", f.stem.replace("memory_", ""))
                title = data.get("title", "") or sid
                msgs = [m for m in data.get("messages", []) if m.get("role") not in ("system", "tool")]
                import datetime
                mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m %H:%M")
                active = " ◆" if sid == orchestrator.memory.session_id else ""
                tbl.add_row(str(i), sid[:8] + active, title[:55], str(len(msgs)), mtime)
                session_ids.append(sid)
            except Exception:
                continue

        console.print()
        console.print(tbl)
        console.print("  [dim]Pour charger : /sessions <numéro>  ex: /sessions 2[/dim]\n")
        return True

    if token.startswith("/sessions "):
        parts = cmd.strip().split(maxsplit=1)
        if len(parts) < 2:
            return True
        arg = parts[1].strip()
        files = sorted(
            MEMORY_DIR.glob("memory_*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:15]
        # Chercher par numéro ou par ID partiel
        target = None
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(files):
                target = files[idx]
        else:
            for f in files:
                if arg in f.stem:
                    target = f
                    break
        if target is None:
            console.print(f"\n  [red]Session introuvable : {arg}[/red]\n")
            return True
        from agent.memory import ConversationMemory as _CM
        loaded = _CM.load_from_file(target)
        orchestrator.memory = loaded
        # Réinitialiser le system prompt si absent
        if not any(m["role"] == "system" for m in loaded.messages):
            orchestrator._inject_system_prompt()
        n = sum(1 for m in loaded.messages if m["role"] not in ("system", "tool"))
        console.print(f"\n  [green]✓[/green] Session [bold]{loaded.session_id}[/bold] chargée — {n} messages.\n")
        return True

    if token == "/profile":
        from agent.profiler import get_profiler
        profiler = get_profiler()
        summary = profiler.get_display_summary()
        if not summary:
            console.print("\n  [dim]Aucun profil détecté. Klody apprend au fil des conversations.[/dim]\n")
            return True

        tbl = Table(
            title="[bold]Profil utilisateur détecté[/bold]",
            box=box.ROUNDED, border_style="magenta", show_lines=True,
        )
        tbl.add_column("Catégorie", style="bold cyan", no_wrap=True)
        tbl.add_column("Détails", style="white")

        if summary.get("top_techs"):
            techs = ", ".join(f"{t} ({c})" for t, c in summary["top_techs"])
            tbl.add_row("Technologies", techs)

        if summary.get("top_categories"):
            cats = ", ".join(f"{c} ({n})" for c, n in summary["top_categories"])
            tbl.add_row("Activités", cats)

        if summary.get("top_tools"):
            tools = ", ".join(f"{t} ({n})" for t, n in summary["top_tools"])
            tbl.add_row("Outils favoris", tools)

        if summary.get("pattern"):
            tbl.add_row("Pattern récurrent", summary["pattern"])

        tbl.add_row("Requêtes totales", str(summary.get("total_requests", 0)))

        console.print()
        console.print(tbl)

        suggestions = profiler.get_suggestions("", [])
        if suggestions:
            console.print()
            sugg_text = "\n".join(f"  [yellow]💡[/yellow] {s}" for s in suggestions[:5])
            console.print(Panel(sugg_text, title="[yellow]Suggestions[/yellow]", border_style="yellow"))
        console.print()
        return True

    if token == "/preview":
        from tools.preview import list_previews
        result = list_previews()
        console.print(Panel(result, title="[magenta]👁  Aperçus[/magenta]", border_style="magenta"))
        return True

    if token == "/export":
        mem = orchestrator.memory
        msgs = [m for m in mem.messages if m["role"] in ("user", "assistant") and m.get("content")]
        if not msgs:
            console.print("\n  [dim]Aucun message à exporter.[/dim]\n")
            return True
        title = mem.title or mem.session_id
        lines = [f"# {title}", "", f"> Session {mem.session_id} · {len(msgs)} messages", "", "---", ""]
        for m in msgs:
            if m["role"] == "user":
                lines += [f"**Vous :** {m['content']}", ""]
            else:
                lines += ["**Klody :**", "", m["content"], "", "---", ""]
        export_path = MEMORY_DIR / f"export_{mem.session_id}.md"
        export_path.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"\n  [green]✓[/green] Session exportée → [bold]{export_path}[/bold]\n")
        return True

    if token == "/status":
        tbl = Table(title="[bold]État du système[/bold]", box=box.ROUNDED, border_style="cyan")
        tbl.add_column("Service", style="bold", no_wrap=True)
        tbl.add_column("État", no_wrap=True)
        tbl.add_column("Détails", style="dim")

        # Backend LLM réellement actif (et non « Ollama » quoi qu'il arrive)
        tbl.add_row(*_sonde_backend_llm())
        tbl.add_row(*_ligne_embeddings())

        # Modèle
        tbl.add_row("Modèle", f"[cyan]{orchestrator.llm.model}[/cyan]", f"~{orchestrator.llm.total_tokens:,} tokens")

        # LibraryBrain
        lb = get_librarybrain_status()
        if lb["up"]:
            tbl.add_row("LibraryBrain", "[green]✓ en ligne[/green]", f"PID {lb.get('pid', '?')}")
        elif lb.get("state") == PROBE_UNAUTHORIZED:
            # Le service tourne mais rejette Klody : ni « en ligne » (aucun appel
            # n'aboutit), ni « hors ligne » (le redémarrer ne réparerait rien).
            tbl.add_row("LibraryBrain", "[yellow]⚠ non autorisé[/yellow]", lb.get("detail", ""))
        else:
            tbl.add_row("LibraryBrain", "[yellow]✗ hors ligne[/yellow]", f"{lb.get('restarts', 0)} redémarrage(s)")

        # Preview
        from tools.preview import _server as pv_server
        if pv_server is not None:
            n_files = len(list(PREVIEW_DIR.glob("*.html"))) if PREVIEW_DIR.exists() else 0
            tbl.add_row("Preview", f"[green]✓ port {PREVIEW_PORT}[/green]", f"{n_files} fichier(s)")
        else:
            tbl.add_row("Preview", "[dim]inactif[/dim]", "Démarre au premier aperçu")

        # Session
        mem = orchestrator.memory
        n = sum(1 for m in mem.messages if m["role"] != "system")
        tbl.add_row("Session", f"[cyan]{mem.session_id}[/cyan]", f"{n} messages")

        console.print()
        console.print(tbl)
        console.print()
        return True

    if token in ("/exit", "/quit", "/q"):
        console.print("\n[bold blue]  ◆  À bientôt ![/bold blue]\n")
        _run_extraction(orchestrator)
        sys.exit(0)

    return False


# ------------------------------------------------------------------ #
# REPL principal                                                       #
# ------------------------------------------------------------------ #

def _run_extraction(orchestrator: Orchestrator) -> None:
    """Extraction silencieuse des faits importants en fin de session."""
    # Note de reprise D'ABORD : elle est instantanée et déterministe, alors que
    # l'extraction fait un appel LLM qui peut échouer — la reprise du lendemain
    # ne doit pas dépendre de lui.
    try:
        from agent.greeting import noter_reprise

        noter_reprise(orchestrator.memory.messages)
    except Exception as exc:  # pragma: no cover - défense du chemin de sortie
        logger.debug("note de reprise non écrite : %s", exc)

    lt = get_long_term_memory()
    facts = extract_and_save(orchestrator.memory.messages, lt)
    if facts:
        console.print(
            f"  [dim magenta]◆ {len(facts)} fait(s) mémorisé(s) automatiquement[/dim magenta]\n"
        )


def repl(orchestrator: Orchestrator, propositions: tuple[str, ...] = ()) -> None:
    """Boucle interactive avec prompt_toolkit (historique, toolbar, suggestions).

    `propositions` : les pistes affichées par l'accueil. Tant qu'aucune entrée
    n'a été traitée, taper leur numéro les lance. La correspondance meurt à la
    première entrée, quelle qu'elle soit : « 2 » au milieu d'une conversation
    redevient la chaîne « 2 », le raccourci ne doit pas capturer une réponse à
    une question de l'agent.
    """

    session: PromptSession = PromptSession(
        history=FileHistory(str(_HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        style=_PT_STYLE,
        bottom_toolbar=make_toolbar(orchestrator),
        refresh_interval=1.0,   # met à jour la toolbar chaque seconde
        mouse_support=False,
    )

    while True:
        try:
            console.print()
            user_input = session.prompt(
                HTML("<prompt>  ❯  </prompt>"),
            ).strip()

            if not user_input:
                continue

            # Propositions de l'accueil : « 1 » lance la première, etc.
            if propositions and user_input.isdigit():
                index = int(user_input) - 1
                if 0 <= index < len(propositions):
                    user_input = propositions[index]
                    console.print(f"  [dim]→ {user_input}[/dim]")
            propositions = ()  # une seule chance, cf. docstring

            # Continuation multi-ligne : "phrase \\" → on concatène
            while user_input.endswith("\\"):
                user_input = user_input[:-1] + " "
                continuation = session.prompt(HTML("<prompt>  …  </prompt>")).strip()
                user_input += continuation

            if user_input.startswith("/"):
                if not handle_special_command(user_input, orchestrator):
                    console.print(f"\n  [red]Commande inconnue :[/red] {user_input}  [dim]→ /help[/dim]\n")
                continue

            # Réponse de l'agent
            console.print()
            console.print(Rule(title="[dim]Klody[/dim]", style="dim blue", align="left"))
            console.print()
            orchestrator.run(user_input)
            _dire_reponse(orchestrator)
            console.print()

        except KeyboardInterrupt:
            # Ctrl+C : vider la ligne en cours sans quitter
            console.print()
            continue
        except EOFError:
            console.print("\n[bold blue]  ◆  À bientôt ![/bold blue]\n")
            _run_extraction(orchestrator)
            sys.exit(0)
        except Exception as e:
            logger.error("Erreur REPL: %s", e, exc_info=True)
            console.print(f"\n  [bold red]Erreur :[/bold red] {e}")
            console.print("  [dim]→ logs/agent.log pour les détails[/dim]\n")


# ------------------------------------------------------------------ #
# Entrée                                                               #
# ------------------------------------------------------------------ #

def _afficher_accueil(accueil: AccueilEnTacheDeFond) -> tuple[str, ...]:
    """Affiche l'accueil interactif et retourne ses propositions.

    Ne doit JAMAIS empêcher la CLI de démarrer : `recuperer()` est déjà borné et
    sans exception, ce garde couvre le rendu lui-même. Retourne les propositions
    pour que le REPL accepte « 1 », « 2 », « 3 » comme première entrée.
    """
    try:
        rendu = accueil.recuperer()
        console.print(Align.center(Text(rendu.salutation, style="italic")))
        if rendu.rappel:
            console.print(Align.center(Text(rendu.rappel, style="dim")))
        if rendu.propositions:
            console.print()
            for i, proposition in enumerate(rendu.propositions, 1):
                console.print(f"    [bold cyan]{i}[/bold cyan]  {proposition}")
            console.print(
                "    [dim]Tape un numéro pour lancer, ou pose ta question.[/dim]"
            )
        _dire_accueil(rendu)
        return rendu.propositions
    except Exception as exc:  # pragma: no cover - défense du chemin de démarrage
        logger.debug("accueil non affiché : %s", exc)
        return ()


# `speak` marque ses succès par cet emoji ; TOUT le reste est un échec rendu
# sous forme de chaîne. Il ne lève jamais d'exception.
_VOIX_SUCCES = "🔊"

# Causes d'échec DÉJÀ signalées, par catégorie. Un ensemble plutôt qu'un
# booléen : signaler chaque réponse serait du spam, mais un booléen unique
# avalait la DEUXIÈME cause — « CLI introuvable » puis, une fois réparée,
# « lecture impossible » : deux pannes distinctes, deux remèdes distincts, et
# l'utilisateur n'aurait vu que la première. Muté en place, donc sans `global`.
_voix_signalees: set[str] = set()

# Chaque cause a un remède différent : les distinguer est ce qui rend le message
# actionnable. Ordre significatif — « lecture impossible » arrive DANS un compte
# rendu de succès (🔊), il doit donc être testé avant le cas nominal.
_CAUSES_VOIX: tuple[tuple[str, str], ...] = (
    ("CLI VocalBrain introuvable", "cli-absente"),
    ("hf download", "modele-absent"),
    ("trop longue", "synthese-lente"),
    ("lecture impossible", "lecteur-muet"),
    ("fichier audio introuvable", "wav-introuvable"),
)


def _cause_voix(rapport: str) -> str:
    """Catégorie d'échec, bornée par construction.

    Dédoublonner sur le texte brut ne marcherait pas : les comptes rendus
    portent une durée et un chemin qui changent à chaque appel, donc chaque
    échec paraîtrait nouveau et le garde anti-spam ne tiendrait pas.
    """
    for marqueur, cause in _CAUSES_VOIX:
        if marqueur in rapport:
            return cause
    return "synthese-echouee"


def _signaler_voix(rapport: str) -> None:
    """Rend visible un échec de `speak`. Sans ça, « aucun son » n'a aucune trace.

    ⚠️ Le défaut réparé ici : `speak` ne lève JAMAIS, il RETOURNE son compte
    rendu — « CLI VocalBrain introuvable », « modèle TTS absent », « lecture
    impossible »… L'appelant jetait cette valeur et n'entourait l'appel que d'un
    `except`, qui ne pouvait rien attraper. Les quatre modes d'échec étaient donc
    strictement indiscernables d'un succès : silence à l'écran, silence au log.

    La règle qui manquait : le silence est acceptable quand personne n'a demandé
    la voix, mais dès que l'utilisateur l'a ACTIVÉE, ne rien dire est la panne.
    """
    if rapport.startswith(_VOIX_SUCCES) and "lecture impossible" not in rapport:
        logger.debug("voix : %s", rapport)
        return

    # Le log garde TOUT : c'est la trace d'enquête. L'écran ne garde que la
    # première occurrence de chaque cause.
    logger.warning("voix : %s", rapport)
    cause = _cause_voix(rapport)
    if cause in _voix_signalees:
        return
    _voix_signalees.add(cause)
    console.print(
        f"\n  [yellow]⚠  Voix indisponible :[/yellow] [dim]{rapport}[/dim]"
        "\n  [dim]→ diagnostic : python scripts/diagnostic_voix.py[/dim]"
    )


def _texte_pour_voix(texte: str) -> str:
    """Prépare une réponse d'agent pour la synthèse : parlable, pas récitable.

    Les blocs de code sont REMPLACÉS par une mention — les lire épellerait de la
    ponctuation pendant trente secondes, et l'écran les affiche de toute façon.
    Le cap est en deçà des 600 caractères de `speak` pour couper sur une phrase
    à nous plutôt que de lui laisser trancher au milieu.
    """
    texte = _BLOC_CODE.sub(" Un bloc de code est affiché à l'écran. ", texte or "")
    texte = _MARQUES_MD.sub("", texte)
    texte = re.sub(r"\s+", " ", texte).strip()
    if len(texte) > 500:
        coupe = texte[:500]
        point = max(coupe.rfind(". "), coupe.rfind("! "), coupe.rfind("? "))
        texte = coupe[: point + 1] if point > 250 else coupe + "…"
    return texte


def _dire_reponse(orchestrator: Orchestrator) -> None:
    """Lit la dernière réponse de l'agent à voix haute, en thread détaché.

    Même contrat que l'accueil vocal : la synthèse est synchrone (~6 s à froid),
    elle ne doit jamais retenir le prompt suivant, et son échec est silencieux à
    l'écran (`speak` rend une chaîne d'erreur, jamais d'exception).
    """
    if not _voix_reponses:
        return
    derniere = next(
        (m.get("content") for m in reversed(orchestrator.memory.messages)
         if m.get("role") == "assistant" and m.get("content")),
        "",
    )
    texte = _texte_pour_voix(str(derniere))
    if not texte:
        return

    def _parler() -> None:
        try:
            from tools.voice import speak

            _signaler_voix(speak(texte, "fr"))
        except Exception as exc:  # pragma: no cover - import de tools.voice cassé
            _signaler_voix(f"speak inutilisable : {exc}")

    threading.Thread(target=_parler, name="klody-voix-reponse", daemon=True).start()


def _dire_accueil(rendu) -> None:
    """Option vocale (accessibilité) : dit l'accueil, propositions numérotées.

    Thread détaché OBLIGATOIRE : la synthèse VocalBrain est synchrone (~6 s à
    froid). La faire attendre sur le chemin de démarrage recréerait exactement
    le gel que l'accueil en tâche de fond existe pour éviter.

    ⚠️ L'échec n'est PLUS silencieux. Il l'a été, et c'était le défaut : quand
    l'utilisateur a explicitement demandé la voix, ne rien dire ne le protège de
    rien — ça lui retire juste tout moyen de comprendre. Cf. `_signaler_voix`.
    """
    if not GREETING_VOICE:
        return

    def _parler() -> None:
        try:
            from tools.voice import speak

            _signaler_voix(speak(rendu.en_texte(), "fr"))
        except Exception as exc:  # pragma: no cover - import de tools.voice cassé
            _signaler_voix(f"speak inutilisable : {exc}")

    threading.Thread(target=_parler, name="klody-accueil-voix", daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Klody Code Ai — Agent de coding local (backend {_backend_label()})",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples:\n"
            "  python main.py                     # nouvelle session\n"
            "  python main.py --resume            # reprendre la dernière session\n"
            "  python main.py --session abc12345  # session précise\n"
        ),
    )
    parser.add_argument("--resume", action="store_true", help="Reprendre la dernière session")
    parser.add_argument("--session", type=str, default=None, metavar="ID")
    args = parser.parse_args()

    memory: ConversationMemory | None = None

    if args.session:
        f = MEMORY_DIR / f"memory_{args.session}.json"
        if f.exists():
            memory = ConversationMemory.load_from_file(f)
            console.print(f"\n  [green]✓[/green] Session [bold]{args.session}[/bold] chargée.")
        else:
            console.print(f"\n  [yellow]Session '{args.session}' introuvable.[/yellow]")

    elif args.resume:
        memory = ConversationMemory.load_latest()
        if memory:
            console.print(f"\n  [green]✓[/green] Session [bold]{memory.session_id}[/bold] reprise.")
        else:
            console.print("\n  [yellow]Aucune session précédente.[/yellow]")

    if memory is None:
        memory = ConversationMemory()

    # Accueil généré : lancé AVANT la construction de l'orchestrator, qui fait la
    # découverte MCP (réseau), et avant la sonde LibraryBrain. Ce temps de
    # démarrage est déjà payé — le thread s'en sert gratuitement, et le cas chaud
    # (0,37 s mesuré) est prêt bien avant qu'on ne l'affiche.
    accueil = AccueilEnTacheDeFond()
    accueil.demarrer()

    orchestrator = Orchestrator(memory)
    print_banner(memory)
    ensure_librarybrain(LIBRARYBRAIN_DIR, LIBRARYBRAIN_URL)
    propositions = _afficher_accueil(accueil)
    console.print()
    repl(orchestrator, propositions)


if __name__ == "__main__":
    main()

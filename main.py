#!/usr/bin/env python3
"""Klody Code Ai — Interface CLI next-level."""

import argparse
import logging
import sys
from pathlib import Path

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

from config import MEMORY_DIR, MODEL_NAME, PROJECT_ROOT, LIBRARYBRAIN_DIR, LIBRARYBRAIN_URL, PREVIEW_DIR, PREVIEW_PORT
from agent.memory import ConversationMemory
from agent.orchestrator import Orchestrator
from agent.long_term_memory import get_long_term_memory
from agent.memory_extractor import extract_and_save
from services import ensure_librarybrain, get_librarybrain_status

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

# ------------------------------------------------------------------ #
# Bannière                                                             #
# ------------------------------------------------------------------ #

def print_banner(memory: ConversationMemory) -> None:
    console.print()

    title = Text()
    title.append("  ◆  ", style="bold blue")
    title.append("KLODY", style="bold white")
    title.append(" CODE AI", style="bold cyan")
    title.append("  ◆", style="bold blue")

    subtitle = Text("  Powered by Ollama · 100% local · privé  ", style="dim")

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
    table.add_row("⚙  Modèle",  f"[green]{MODEL_NAME}[/green]")
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
  [cyan]/model <nom>[/cyan]       Changer de modèle  [dim]ex: /model qwen2.5-coder:7b[/dim]
  [cyan]/sessions[/cyan]          Lister et charger une session précédente
  [cyan]/skills[/cyan]            Lister les compétences mémorisées
  [cyan]/tokens[/cyan]            Afficher le compteur de tokens
  [cyan]/preview[/cyan]           Aperçus HTML disponibles + URLs
  [cyan]/export[/cyan]            Exporter la session en Markdown
  [cyan]/profile[/cyan]           Profil utilisateur détecté (techs, patterns)
  [cyan]/status[/cyan]            État du système (Ollama, LibraryBrain, Preview)
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
        import httpx
        from config import OLLAMA_BASE_URL

        tbl = Table(title="[bold]État du système[/bold]", box=box.ROUNDED, border_style="cyan")
        tbl.add_column("Service", style="bold", no_wrap=True)
        tbl.add_column("État", no_wrap=True)
        tbl.add_column("Détails", style="dim")

        # Ollama
        try:
            r = httpx.get(OLLAMA_BASE_URL.replace("/v1", "") + "/api/tags", timeout=2.0)
            models = [m["name"] for m in r.json().get("models", [])]
            tbl.add_row("Ollama", "[green]✓ en ligne[/green]", f"{len(models)} modèle(s)")
        except Exception:
            tbl.add_row("Ollama", "[red]✗ hors ligne[/red]", "ollama serve")

        # Modèle
        tbl.add_row("Modèle", f"[cyan]{orchestrator.llm.model}[/cyan]", f"~{orchestrator.llm.total_tokens:,} tokens")

        # LibraryBrain
        lb = get_librarybrain_status()
        if lb["up"]:
            tbl.add_row("LibraryBrain", "[green]✓ en ligne[/green]", f"PID {lb.get('pid', '?')}")
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
    lt = get_long_term_memory()
    facts = extract_and_save(orchestrator.memory.messages, lt)
    if facts:
        console.print(
            f"  [dim magenta]◆ {len(facts)} fait(s) mémorisé(s) automatiquement[/dim magenta]\n"
        )


def repl(orchestrator: Orchestrator) -> None:
    """Boucle interactive avec prompt_toolkit (historique, toolbar, suggestions)."""

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

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Klody Code Ai — Agent de coding local (Ollama)",
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

    orchestrator = Orchestrator(memory)
    print_banner(memory)
    ensure_librarybrain(LIBRARYBRAIN_DIR, LIBRARYBRAIN_URL)
    console.print()
    repl(orchestrator)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Point d'entrée CLI — Klody Code Ai."""

import argparse
import logging
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import MEMORY_DIR, MODEL_NAME, PROJECT_ROOT
from agent.memory import ConversationMemory
from agent.orchestrator import Orchestrator

logger = logging.getLogger(__name__)
console = Console()

BANNER = Text.from_markup(
    "\n"
    "[bold blue]╔══════════════════════════════════════════╗[/bold blue]\n"
    "[bold blue]║[/bold blue]  [bold white]🤖  Klody Code Ai[/bold white]                    [bold blue]║[/bold blue]\n"
    "[bold blue]║[/bold blue]  [dim]Powered by Ollama · 100% local · privé[/dim]   [bold blue]║[/bold blue]\n"
    "[bold blue]╚══════════════════════════════════════════╝[/bold blue]\n"
)

HELP_TEXT = """
[bold]Commandes spéciales :[/bold]

  [cyan]/help[/cyan]            Afficher cette aide
  [cyan]/clear[/cyan]           Effacer l'historique de la session courante
  [cyan]/memory[/cyan]          Afficher les statistiques de mémoire
  [cyan]/model[/cyan]           Afficher le modèle actif
  [cyan]/model <nom>[/cyan]     Changer de modèle (ex: /model qwen2.5-coder:7b)
  [cyan]/exit[/cyan]            Quitter l'agent
  [cyan]Ctrl+C[/cyan]           Quitter l'agent
"""


def print_banner(memory: ConversationMemory) -> None:
    console.print(BANNER)

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("clé", style="dim", width=12)
    table.add_column("valeur", style="bold")

    non_system = sum(1 for m in memory.messages if m["role"] != "system")
    table.add_row("Modèle", f"[green]{MODEL_NAME}[/green]")
    table.add_row("Projet", str(PROJECT_ROOT))
    table.add_row("Session", memory.session_id)
    table.add_row("Messages", str(non_system))

    console.print(table)
    console.print("[dim]Tapez /help pour l'aide · /exit pour quitter[/dim]\n")


def handle_special_command(cmd: str, orchestrator: Orchestrator) -> bool:
    """
    Gère les commandes spéciales /xxx.
    Retourne True si traitée, False si inconnue.
    """
    token = cmd.strip().lower()

    if token == "/help":
        console.print(HELP_TEXT)
        return True

    if token == "/clear":
        orchestrator.memory.clear()
        # clear() préserve le system prompt, pas besoin de ré-injecter
        console.print("[green]✓ Historique effacé.[/green]")
        return True

    if token == "/memory":
        stats = orchestrator.memory.stats()
        table = Table(title="Mémoire de session", box=box.ROUNDED, border_style="blue")
        table.add_column("Clé", style="cyan", no_wrap=True)
        table.add_column("Valeur")
        for k, v in stats.items():
            table.add_row(k, str(v))
        console.print(table)
        return True

    if token.startswith("/model"):
        parts = cmd.strip().split(maxsplit=1)
        if len(parts) == 2:
            new_model = parts[1].strip()
            orchestrator.llm.model = new_model
            console.print(f"[green]✓ Modèle changé:[/green] [bold]{new_model}[/bold]")
        else:
            console.print(
                f"[cyan]Modèle actif:[/cyan] [bold]{orchestrator.llm.model}[/bold]"
            )
        return True

    if token in ("/exit", "/quit", "/q"):
        console.print("\n[bold blue]À bientôt ! 👋[/bold blue]\n")
        sys.exit(0)

    return False


def repl(orchestrator: Orchestrator) -> None:
    """Boucle REPL interactive."""
    while True:
        try:
            console.print()
            user_input = console.input("[bold green]Vous >[/bold green] ").strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                if not handle_special_command(user_input, orchestrator):
                    console.print(
                        f"[red]Commande inconnue:[/red] {user_input} — tapez /help"
                    )
                continue

            console.print("\n[bold blue]Klody >[/bold blue]")
            orchestrator.run(user_input)

        except KeyboardInterrupt:
            console.print("\n\n[bold blue]À bientôt ! 👋[/bold blue]\n")
            sys.exit(0)
        except EOFError:
            sys.exit(0)
        except Exception as e:
            logger.error("Erreur REPL: %s", e, exc_info=True)
            console.print(f"\n[bold red]Erreur:[/bold red] {e}")
            console.print("[dim]Consultez logs/agent.log pour les détails.[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Klody Code Ai — Agent de coding local basé sur Ollama",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples:\n"
            "  python main.py                     # nouvelle session\n"
            "  python main.py --resume            # reprendre la dernière session\n"
            "  python main.py --session abc12345  # reprendre une session précise\n"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reprendre la dernière session",
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        metavar="ID",
        help="ID de session à charger (ex: abc12345)",
    )
    args = parser.parse_args()

    # --- Chargement ou création de la mémoire ---
    memory: ConversationMemory | None = None

    if args.session:
        session_file = MEMORY_DIR / f"memory_{args.session}.json"
        if session_file.exists():
            memory = ConversationMemory.load_from_file(session_file)
            console.print(f"[green]✓ Session[/green] [bold]{args.session}[/bold] chargée.")
        else:
            console.print(
                f"[yellow]Session '{args.session}' introuvable — nouvelle session.[/yellow]"
            )

    elif args.resume:
        memory = ConversationMemory.load_latest()
        if memory:
            console.print(
                f"[green]✓ Session[/green] [bold]{memory.session_id}[/bold] reprise."
            )
        else:
            console.print("[yellow]Aucune session précédente — nouvelle session.[/yellow]")

    if memory is None:
        memory = ConversationMemory()

    orchestrator = Orchestrator(memory)
    print_banner(memory)
    repl(orchestrator)


if __name__ == "__main__":
    main()

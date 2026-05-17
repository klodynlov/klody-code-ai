import json
import logging

from rich.console import Console
from rich.panel import Panel

from agent.llm import LLMClient, SYSTEM_PROMPT
from agent.memory import ConversationMemory
from tools.file_manager import FileManager, SandboxViolation
from tools.registry import get_tools
from tools.search import Search
from tools.terminal import CommandBlocked, Terminal
from config import MAX_ITERATIONS, PROJECT_ROOT

logger = logging.getLogger(__name__)
console = Console()


class Orchestrator:
    def __init__(self, memory: ConversationMemory):
        self.memory = memory
        self.llm = LLMClient()
        self.file_manager = FileManager()
        self.terminal = Terminal()
        self.search = Search()
        self.tools = get_tools()

        if not any(m["role"] == "system" for m in memory.messages):
            self._inject_system_prompt()

    def _inject_system_prompt(self) -> None:
        content = f"{SYSTEM_PROMPT}\n\nDossier projet actif: {PROJECT_ROOT}"
        self.memory.messages.insert(0, {
            "role": "system",
            "content": content,
            "timestamp": None,
        })

    # ------------------------------------------------------------------ #
    # Routing des outils                                                   #
    # ------------------------------------------------------------------ #

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        logger.info("Outil: %s | Args: %s", tool_name, tool_args)
        try:
            if tool_name == "read_file":
                return self.file_manager.read_file(tool_args["path"])

            if tool_name == "write_file":
                return self.file_manager.write_file(
                    tool_args["path"], tool_args["content"]
                )

            if tool_name == "list_files":
                return self.file_manager.list_files(
                    tool_args.get("path", "."),
                    tool_args.get("recursive", False),
                )

            if tool_name == "execute_command":
                return self.terminal.execute_command(
                    tool_args["command"],
                    tool_args.get("reason", ""),
                )

            if tool_name == "search_in_files":
                return self.search.search_in_files(
                    tool_args["pattern"],
                    tool_args.get("path", "."),
                    tool_args.get("file_pattern", ""),
                    tool_args.get("case_sensitive", True),
                )

            return f"ERREUR: Outil inconnu '{tool_name}'"

        except SandboxViolation as e:
            logger.warning("Violation sandbox (%s): %s", tool_name, e)
            return f"ERREUR SÉCURITÉ: {e}"
        except CommandBlocked as e:
            logger.warning("Commande bloquée (%s): %s", tool_name, e)
            return f"ERREUR SÉCURITÉ: {e}"
        except FileNotFoundError as e:
            return f"ERREUR: Fichier introuvable — {e}"
        except Exception as e:
            logger.error("Erreur dans %s: %s", tool_name, e, exc_info=True)
            return f"ERREUR: {e}"

    # ------------------------------------------------------------------ #
    # Boucle ReAct principale                                              #
    # ------------------------------------------------------------------ #

    def run(self, user_input: str) -> None:
        """
        Boucle ReAct : Thought (LLM) → Action (outil) → Observation → repeat.
        S'arrête quand le LLM répond en texte (sans tool calls) ou après
        MAX_ITERATIONS cycles.
        """
        self.memory.add_message("user", user_input)

        for iteration in range(MAX_ITERATIONS):
            if iteration > 0:
                console.print(
                    f"\n[dim]─── Itération {iteration + 1}/{MAX_ITERATIONS} ───[/dim]"
                )

            messages = self.memory.get_messages_for_api()
            content, tool_calls = self.llm.stream_chat(messages, tools=self.tools)

            if tool_calls:
                # Sauvegarder la décision du LLM (message avec tool calls)
                self.memory.add_tool_call_message([
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ])

                for tc in tool_calls:
                    tool_name = tc["function"]["name"]
                    tool_id = tc["id"]

                    try:
                        tool_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        tool_args = {}
                        logger.error(
                            "JSON invalide pour %s: %s",
                            tool_name,
                            tc["function"]["arguments"],
                        )

                    args_preview = ", ".join(
                        f"{k}={repr(v)[:40]}" for k, v in tool_args.items()
                    )
                    console.print(
                        f"\n[cyan]🔧[/cyan] [bold]{tool_name}[/bold]"
                        f"([dim]{args_preview}[/dim])"
                    )

                    result = self._execute_tool(tool_name, tool_args)

                    preview = result[:300] + "…" if len(result) > 300 else result
                    console.print(Panel(
                        f"[green]{preview}[/green]",
                        title=f"[green]✓ {tool_name}[/green]",
                        border_style="green",
                        padding=(0, 1),
                    ))

                    self.memory.add_tool_result(tool_id, tool_name, result)

                # Prochain tour : le LLM traite les résultats des outils
                continue

            else:
                # Réponse texte finale — l'agent a terminé
                if content:
                    self.memory.add_message("assistant", content)
                break

        else:
            console.print(
                f"\n[yellow]⚠ Limite d'itérations atteinte ({MAX_ITERATIONS}).[/yellow]"
            )
            logger.warning("Limite d'itérations atteinte: %d", MAX_ITERATIONS)

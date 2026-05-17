import json
import logging
import re
import uuid
from typing import Optional

from openai import OpenAI, APIConnectionError, APITimeoutError
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text

from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, MODEL_NAME

logger = logging.getLogger(__name__)
console = Console()

SYSTEM_PROMPT = """\
Tu es un agent de coding expert nommé Klody. Tu travailles UNIQUEMENT dans \
le dossier projet qui t'est assigné. Pour chaque tâche :
1. Analyse le contexte (lis les fichiers pertinents d'abord)
2. Planifie les étapes avant d'agir
3. Exécute étape par étape
4. Vérifie le résultat de chaque action
5. Rends compte clairement de ce que tu as fait

Tu as accès aux outils : read_file, write_file, list_files, \
execute_command, search_in_files.
Ne modifie jamais un fichier sans l'avoir lu avant.
Avant toute commande bash, explique pourquoi tu en as besoin.\
"""


def _has_markdown(text: str) -> bool:
    """Détecte si le texte contient du Markdown significatif."""
    markers = ("```", "**", "##", "# ", "- ", "* ", "> ", "| ")
    return any(m in text for m in markers)


class LLMClient:
    def __init__(self, model: str = MODEL_NAME):
        self.model = model
        self.client = OpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key=OLLAMA_API_KEY,
        )
        # Compteur de tokens approximatif (session courante)
        self.total_tokens: int = 0

    def stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> tuple[str, Optional[list[dict]]]:
        """
        Envoie les messages et streame la réponse avec :
        - Spinner "Klody réfléchit..." avant le premier token
        - Rendu Markdown progressif pendant le streaming
        - Fallback : parse les tool calls émis en JSON texte
        """
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.1,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        full_content = ""
        raw_tool_calls: dict[int, dict] = {}

        try:
            stream = self.client.chat.completions.create(**params)

            first_token = False

            # Phase 1 : spinner pendant que le modèle charge
            spinner = Spinner("dots2", text=Text(" Klody réfléchit…", style="dim cyan"))

            with Live(spinner, console=console, refresh_per_second=12, transient=True):
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    if delta.content:
                        full_content += delta.content
                        first_token = True
                        # On sort du spinner dès le 1er token
                        break

                    if delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            self._accumulate_tool_call(raw_tool_calls, tc_chunk)
                        first_token = True
                        break

            # Phase 2 : streaming Markdown progressif (si on a du contenu texte)
            if full_content:
                with Live(
                    Markdown(full_content),
                    console=console,
                    refresh_per_second=12,
                    vertical_overflow="visible",
                ) as live:
                    for chunk in stream:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta

                        if delta.content:
                            full_content += delta.content
                            # Re-render Markdown à chaque token
                            live.update(Markdown(full_content))

                        if delta.tool_calls:
                            for tc_chunk in delta.tool_calls:
                                self._accumulate_tool_call(raw_tool_calls, tc_chunk)

            else:
                # Pas de contenu texte — continuer à collecter les tool calls
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_content += delta.content
                    if delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            self._accumulate_tool_call(raw_tool_calls, tc_chunk)

            # Ligne de séparation discrète après la réponse
            if full_content:
                console.print(Rule(style="dim blue"))

            # Estimation tokens
            self.total_tokens += len(full_content) // 4
            for m in messages:
                c = m.get("content") or ""
                self.total_tokens += len(c) // 4

            tool_calls = list(raw_tool_calls.values()) if raw_tool_calls else None

            # Fallback : tool call émis en JSON texte (qwen2.5-coder via Ollama)
            if not tool_calls and full_content and tools:
                valid_names = {t["function"]["name"] for t in tools}
                parsed = self._parse_text_tool_calls(full_content, valid_names)
                if parsed:
                    tool_calls = parsed
                    full_content = ""

            if full_content:
                logger.info("Réponse LLM: %d chars", len(full_content))
            if tool_calls:
                logger.info("Tool calls: %s", [tc["function"]["name"] for tc in tool_calls])

            return full_content, tool_calls

        except APIConnectionError as e:
            logger.error("Ollama inaccessible: %s", e)
            console.print(
                "\n[bold red]✗ Impossible de joindre Ollama.[/bold red]\n"
                "[dim]  → ollama serve[/dim]\n"
            )
            raise
        except APITimeoutError as e:
            logger.error("Timeout LLM: %s", e)
            console.print("\n[bold red]✗ Timeout du modèle.[/bold red]\n")
            raise
        except Exception as e:
            logger.error("Erreur LLM: %s", e)
            raise

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _accumulate_tool_call(self, raw: dict, tc_chunk) -> None:
        idx = tc_chunk.index
        if idx not in raw:
            raw[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        if tc_chunk.id:
            raw[idx]["id"] += tc_chunk.id
        if tc_chunk.function:
            if tc_chunk.function.name:
                raw[idx]["function"]["name"] += tc_chunk.function.name
            if tc_chunk.function.arguments:
                raw[idx]["function"]["arguments"] += tc_chunk.function.arguments

    def _parse_text_tool_calls(
        self, content: str, valid_tool_names: set[str]
    ) -> Optional[list[dict]]:
        """
        Fallback : parse les tool calls émis comme JSON texte.
        Gère objet unique, liste, blocs ```json```.
        """
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()

        if not text.startswith(("{", "[")):
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None

        def make_call(item: dict) -> Optional[dict]:
            name = item.get("name", "")
            if name not in valid_tool_names:
                return None
            args = item.get("arguments", item.get("parameters", {}))
            return {
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                },
            }

        if isinstance(data, dict):
            call = make_call(data)
            return [call] if call else None

        if isinstance(data, list):
            calls = [c for item in data if isinstance(item, dict) for c in [make_call(item)] if c]
            return calls if calls else None

        return None

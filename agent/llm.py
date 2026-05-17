import json
import logging
import re
import uuid
from typing import Optional

from openai import OpenAI, APIConnectionError, APITimeoutError
from rich.console import Console
from rich.live import Live
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


class LLMClient:
    def __init__(self, model: str = MODEL_NAME):
        self.model = model
        self.client = OpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key=OLLAMA_API_KEY,
        )

    def stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> tuple[str, Optional[list[dict]]]:
        """
        Envoie les messages au LLM et streame la réponse token par token.
        Retourne (texte_complet, tool_calls_ou_None).

        Fallback automatique : si le modèle retourne un tool call en JSON texte
        (comportement de qwen2.5-coder via Ollama), il est parsé et converti.
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
        # Dictionnaire indexé par position pour reconstruire les tool calls fragmentés
        raw_tool_calls: dict[int, dict] = {}

        try:
            stream = self.client.chat.completions.create(**params)

            live_text = Text()
            with Live(live_text, console=console, refresh_per_second=15) as live:
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    if delta.content:
                        full_content += delta.content
                        live_text.append(delta.content)
                        live.update(live_text)

                    if delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            idx = tc_chunk.index
                            if idx not in raw_tool_calls:
                                raw_tool_calls[idx] = {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc_chunk.id:
                                raw_tool_calls[idx]["id"] += tc_chunk.id
                            if tc_chunk.function:
                                if tc_chunk.function.name:
                                    raw_tool_calls[idx]["function"]["name"] += tc_chunk.function.name
                                if tc_chunk.function.arguments:
                                    raw_tool_calls[idx]["function"]["arguments"] += tc_chunk.function.arguments

            tool_calls = list(raw_tool_calls.values()) if raw_tool_calls else None

            # Fallback : certains modèles (qwen2.5-coder via Ollama) retournent
            # les tool calls comme texte JSON au lieu du format natif OpenAI
            if not tool_calls and full_content and tools:
                valid_names = {t["function"]["name"] for t in tools}
                parsed = self._parse_text_tool_calls(full_content, valid_names)
                if parsed:
                    tool_calls = parsed
                    full_content = ""  # ce n'est pas du texte à afficher

            if full_content:
                logger.info("Réponse LLM: %d caractères", len(full_content))
            if tool_calls:
                logger.info("Tool calls: %s", [tc["function"]["name"] for tc in tool_calls])

            return full_content, tool_calls

        except APIConnectionError as e:
            logger.error("Ollama inaccessible: %s", e)
            console.print(
                "\n[bold red]Erreur: Impossible de joindre Ollama.[/bold red]\n"
                "[dim]Vérifiez que le serveur tourne : ollama serve[/dim]"
            )
            raise
        except APITimeoutError as e:
            logger.error("Timeout LLM: %s", e)
            console.print("\n[bold red]Erreur: Timeout du modèle.[/bold red]")
            raise
        except Exception as e:
            logger.error("Erreur LLM inattendue: %s", e)
            raise

    def _parse_text_tool_calls(
        self, content: str, valid_tool_names: set[str]
    ) -> Optional[list[dict]]:
        """
        Détecte et parse les tool calls émis comme texte JSON.
        Gère : objet unique, liste d'objets, blocs markdown ```json```.
        """
        text = content.strip()
        # Retirer les blocs de code markdown éventuels
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

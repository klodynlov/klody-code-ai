import json
import logging
import re
import uuid
from typing import Callable, Optional

from openai import OpenAI, APIConnectionError, APITimeoutError
from rich.console import Console
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text
from rich.live import Live

from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, MODEL_NAME, MODEL_FALLBACK

logger = logging.getLogger(__name__)
console = Console()

SYSTEM_PROMPT = """\
Tu es Klody, un agent de coding expert. Réponds en français.

RÈGLE CRITIQUE : N'utilise les outils QUE si la tâche l'exige explicitement. \
Pour les questions générales, la conversation, ou les explications : \
réponds DIRECTEMENT sans outil.

Quand tu dois agir sur le code :
1. Lis les fichiers concernés avant de les modifier
2. Exécute étape par étape
3. Vérifie chaque action
4. Rends compte clairement

Apprentissage des pratiques utilisateur :
- Si l'utilisateur te demande d'analyser ses exports LLM, utilise list_imports \
puis import_llm_export pour lire et analyser chaque fichier.
- Après analyse, utilise save_skill pour mémoriser les patterns importants \
(langages préférés, frameworks, habitudes de code, questions récurrentes).
- Enrichis ta compréhension de l'utilisateur à chaque import.

Dépôts GitHub et bonnes pratiques :
- Tu peux lire n'importe quel dépôt GitHub avec browse_repo et read_github_file.
- Utilise extract_best_practices pour analyser un dépôt et identifier ses patterns.
- Après analyse, utilise save_skill pour mémoriser les bonnes pratiques utiles.
- Utilise index_github_repo pour ajouter un dépôt à LibraryBrain (recherche RAG).
- Pour travailler sur du code : clone_github_repo le clone et l'ouvre dans PyCharm.
- Pour créer un projet inspiré d'un dépôt : extract_best_practices → create_project → \
adapte avec write_file en lisant le code source via read_github_file.

Ne modifie jamais un fichier sans l'avoir lu. \
Avant toute commande bash, explique pourquoi.\
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
        token_callback: Optional[Callable[[str], None]] = None,
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
                        if token_callback:
                            token_callback(delta.content)
                        first_token = True
                        break

                    if delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            self._accumulate_tool_call(raw_tool_calls, tc_chunk)
                        first_token = True
                        break

            # Phase 2 : streaming token par token — texte brut affiché au fil de l'eau
            if full_content:
                # Premier token déjà dans full_content — l'afficher sans markup
                console.print(full_content, end="", markup=False, highlight=False)

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    full_content += delta.content
                    console.print(delta.content, end="", markup=False, highlight=False)
                    if token_callback:
                        token_callback(delta.content)

                if delta.tool_calls:
                    for tc_chunk in delta.tool_calls:
                        self._accumulate_tool_call(raw_tool_calls, tc_chunk)

            # Saut de ligne + séparateur discret après la réponse texte
            if full_content:
                console.print()
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
            # Bascule automatique sur le modèle de secours si disponible
            if self.model != MODEL_FALLBACK:
                logger.warning("Timeout — bascule sur '%s'", MODEL_FALLBACK)
                console.print(
                    f"\n[yellow]⚠  Timeout — bascule automatique sur [bold]{MODEL_FALLBACK}[/bold][/yellow]\n"
                )
                self.model = MODEL_FALLBACK
                return self.stream_chat(messages, tools, token_callback)
            console.print("\n[bold red]✗ Timeout du modèle.[/bold red]\n")
            raise
        except Exception as e:
            err_str = str(e).lower()
            # Modèle introuvable → bascule sur le modèle de secours
            if ("not found" in err_str or "does not exist" in err_str) and self.model != MODEL_FALLBACK:
                logger.warning("Modèle '%s' introuvable — bascule sur '%s'", self.model, MODEL_FALLBACK)
                console.print(
                    f"\n[yellow]⚠  Modèle [bold]{self.model}[/bold] introuvable — "
                    f"bascule sur [bold]{MODEL_FALLBACK}[/bold][/yellow]\n"
                )
                self.model = MODEL_FALLBACK
                return self.stream_chat(messages, tools, token_callback)
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

    def extract_mixed_tool_call(
        self, content: str, valid_tool_names: set[str]
    ) -> tuple[str, Optional[list[dict]]]:
        """
        Extrait un tool call JSON depuis un contenu mixte (texte + JSON collés).
        Retourne (texte_avant, tool_calls) ou (content, None) si rien trouvé.
        """
        # Essai pure JSON d'abord
        pure = self._parse_text_tool_calls(content, valid_tool_names)
        if pure:
            return "", pure

        # Chercher le début d'un JSON tool call dans le contenu
        names_pattern = "|".join(re.escape(n) for n in valid_tool_names)
        pattern = rf'\{{"name":\s*"(?:{names_pattern})"'
        matches = list(re.finditer(pattern, content))
        if not matches:
            return content, None

        # Prendre le dernier match et tenter de parser depuis là
        start = matches[-1].start()
        text_part = content[:start].rstrip()
        json_part = content[start:]

        parsed = self._parse_text_tool_calls(json_part, valid_tool_names)
        if parsed:
            return text_part, parsed

        return content, None

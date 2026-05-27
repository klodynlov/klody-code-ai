"""Router adaptatif — Roadmap v2 #4.

Classifie un prompt utilisateur en (difficulty, task_type) AVANT le ReAct loop,
pour permettre à l'orchestrator d'adapter sa stratégie :
- max_iterations selon la complexité
- use_planner: True uniquement si hard ou multi-fichier
- (futur #5) hot-swap du system prompt selon task_type
- (futur #7) best-of-N conditionnel si hard
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Literal, Optional

from openai import OpenAI

from config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL

logger = logging.getLogger(__name__)


Difficulty = Literal["easy", "medium", "hard"]
TaskType = Literal["edit", "refactor", "bug_fix", "feature", "explain"]


@dataclass
class RoutingDecision:
    """Décision de routage retournée par le Router."""

    difficulty: Difficulty
    task_type: TaskType
    max_iterations: int
    use_planner: bool
    use_best_of_n: bool
    reasoning: str
    raw_response: str = ""  # pour debug/eval

    def to_dict(self) -> dict:
        return asdict(self)


# --- Defaults selon difficulty/task_type ----------------------------------- #

_MAX_ITER = {"easy": 3, "medium": 8, "hard": 10}


def _decide_strategy(difficulty: Difficulty, task_type: TaskType) -> dict:
    """Dérive use_planner / use_best_of_n / max_iter depuis la classif.

    Règles :
    - Planner si hard ou (medium + feature/refactor) → tâches multi-étapes
    - Best-of-N seulement si hard
    - max_iter dérivé de la difficulty
    """
    use_planner = (difficulty == "hard") or (
        difficulty == "medium" and task_type in ("feature", "refactor")
    )
    use_best_of_n = difficulty == "hard"
    return {
        "max_iterations": _MAX_ITER[difficulty],
        "use_planner": use_planner,
        "use_best_of_n": use_best_of_n,
    }


# --- Prompt système du Router ---------------------------------------------- #

_ROUTER_SYSTEM = """\
Tu es un router de tâches. Classifie chaque demande de coding.

Réponds UNIQUEMENT par un objet JSON valide, sans markdown, sans texte avant ou après :
{"difficulty": "easy|medium|hard", "task_type": "edit|refactor|bug_fix|feature|explain", "reasoning": "phrase courte"}

DIFFICULTY :
- easy   : 1 fichier, modification localisée (<30s). Rename, fix typo, add import, add docstring, add 1 test simple.
- medium : 1-3 fichiers, refactor léger ou bug à corriger via test (<2min). Extract function, add type hints, fix failing test, add CLI arg, add logging.
- hard   : multi-fichier, debug subtil, perf, async, architecture (>2min). Race condition, optimize O(n²), migrate sync→async, full endpoint, debug suite.

TASK_TYPE :
- edit     : rename, fix typo, format. Pas de logique nouvelle.
- refactor : extract function, restructure, style change. Code équivalent réorganisé.
- bug_fix  : un test échoue, un bug rapporté → corriger le code (pas le test).
- feature  : ajouter du code nouveau (fonction, classe, endpoint, test).
- explain  : question, lecture, analyse. Pas de modification de fichier.

Exemples :
- "renomme `usr` en `user` dans app.py" → {"difficulty":"easy","task_type":"edit","reasoning":"rename localisé 1 fichier"}
- "ajoute une docstring à compute_area" → {"difficulty":"easy","task_type":"edit","reasoning":"docstring 1 fonction"}
- "le test test_calc.py échoue, corrige le code" → {"difficulty":"medium","task_type":"bug_fix","reasoning":"bug fix avec test existant"}
- "extrais ce code dupliqué en fonction" → {"difficulty":"medium","task_type":"refactor","reasoning":"refactor avec extraction"}
- "il y a une race condition dans worker.py" → {"difficulty":"hard","task_type":"bug_fix","reasoning":"debug async complexe"}
- "ajoute un endpoint FastAPI complet avec test" → {"difficulty":"hard","task_type":"feature","reasoning":"multi-fichier route+model+test"}
- "explique-moi comment fonctionne ce module" → {"difficulty":"easy","task_type":"explain","reasoning":"lecture sans modif"}
"""


class Router:
    """Router LLM léger qui classifie une demande coding."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or LLM_MODEL
        self.client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

    def classify(self, user_prompt: str) -> RoutingDecision:
        """Appelle le LLM et retourne une RoutingDecision.

        En cas d'échec de parsing, retourne un fallback safe (medium / explain).
        """
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _ROUTER_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=200,
                stream=False,
            )
            raw = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.error("Router LLM call failed: %s", exc)
            return self._fallback(reason=f"LLM error: {exc}", raw="")

        return self._parse_response(raw, user_prompt)

    def _parse_response(self, raw: str, user_prompt: str) -> RoutingDecision:
        """Parse la réponse du LLM en RoutingDecision.

        Robuste à : markdown ```json```, texte avant/après, JSON nested.
        """
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)

        # Cherche un objet JSON complet dans le texte
        match = re.search(r"\{[^{}]*\"difficulty\"[^{}]*\}", text, re.DOTALL)
        json_str = match.group(0) if match else text

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Router: JSON parse failed on: %s", raw[:120])
            return self._fallback(reason="invalid JSON", raw=raw)

        difficulty = data.get("difficulty", "").lower()
        task_type = data.get("task_type", "").lower()
        reasoning = data.get("reasoning", "")[:200]

        if difficulty not in ("easy", "medium", "hard"):
            return self._fallback(reason=f"invalid difficulty: {difficulty}", raw=raw)
        if task_type not in ("edit", "refactor", "bug_fix", "feature", "explain"):
            return self._fallback(reason=f"invalid task_type: {task_type}", raw=raw)

        strategy = _decide_strategy(difficulty, task_type)
        return RoutingDecision(
            difficulty=difficulty,  # type: ignore[arg-type]
            task_type=task_type,    # type: ignore[arg-type]
            reasoning=reasoning,
            raw_response=raw,
            **strategy,
        )

    @staticmethod
    def _fallback(reason: str, raw: str) -> RoutingDecision:
        """Fallback safe : medium/explain, planner ON, pas de best-of-N."""
        strategy = _decide_strategy("medium", "explain")
        return RoutingDecision(
            difficulty="medium",
            task_type="explain",
            reasoning=f"[fallback: {reason}]",
            raw_response=raw,
            **strategy,
        )

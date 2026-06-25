"""Router adaptatif — Roadmap v2 #4.

Classifie un prompt utilisateur en (difficulty, task_type) AVANT le ReAct loop,
pour permettre à l'orchestrator d'adapter sa stratégie :
- max_iterations selon la complexité
- use_planner: True uniquement si hard ou multi-fichier
- (futur #5) hot-swap du system prompt selon task_type
- (futur #7) best-of-N conditionnel si hard

Contrainte JSON conforme à docs/json-constraint-policy.md : validation Pydantic +
retry borné (réinjection d'un message de correction) AVANT le fallback safe — jamais
« strip + espoir ».
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Literal

from config import LLM_API_KEY, LLM_BASE_URL, LLM_HTTP_TIMEOUT, LLM_MAX_RETRIES, LLM_MODEL
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


Difficulty = Literal["easy", "medium", "hard"]
TaskType = Literal["edit", "refactor", "bug_fix", "feature", "explain", "self_dev"]

# Nb d'essais LLM supplémentaires après l'appel initial sur échec de validation.
# 1 appel initial + _ROUTER_MAX_RETRIES retries = (_ROUTER_MAX_RETRIES + 1) appels max.
_ROUTER_MAX_RETRIES = 2


class _RouterClassification(BaseModel):
    """Schéma strict des 3 champs émis par le LLM, validés par Pydantic.

    Les `Literal` rejettent nativement toute valeur hors-domaine → ValidationError.
    Ne couvre PAS les champs dérivés (max_iterations/use_planner/use_best_of_n) qui
    restent calculés par `_decide_strategy` dans le dataclass public RoutingDecision.
    Les champs en trop émis par le modèle sont ignorés (comportement Pydantic v2).
    """

    difficulty: Difficulty
    task_type: TaskType
    reasoning: str = ""


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

_MAX_ITER = {"easy": 6, "medium": 14, "hard": 25}


def _decide_strategy(difficulty: Difficulty, task_type: TaskType) -> dict:
    """Dérive use_planner / use_best_of_n / max_iter depuis la classif.

    Règles :
    - Planner si hard, ou (medium + feature/refactor/self_dev) → multi-étapes
    - Best-of-N si hard OU self_dev (changements de code critique)
    - max_iter dérivé de la difficulty
    """
    use_planner = (difficulty == "hard") or (
        difficulty == "medium" and task_type in ("feature", "refactor", "self_dev")
    )
    use_best_of_n = difficulty == "hard" or task_type == "self_dev"
    return {
        "max_iterations": _MAX_ITER[difficulty],
        "use_planner": use_planner,
        "use_best_of_n": use_best_of_n,
    }


# --- Prompts du Router ------------------------------------------------------ #

_ROUTER_SYSTEM = """\
Tu es un router de tâches. Classifie chaque demande de coding.

Réponds UNIQUEMENT par un objet JSON valide, sans markdown, sans texte avant ou après :
{"difficulty": "easy|medium|hard", "task_type": "edit|refactor|bug_fix|feature|explain|self_dev", "reasoning": "phrase courte"}

DIFFICULTY :
- easy   : 1 fichier, modification localisée (<30s). Rename, fix typo, add import, add docstring, add 1 test simple.
- medium : 1-3 fichiers, refactor léger ou bug à corriger via test (<2min). Extract function, add type hints, fix failing test, add CLI arg, add logging.
- hard   : multi-fichier, debug subtil, perf, async, architecture (>2min). Race condition, optimize O(n²), migrate sync→async, full endpoint, debug suite, self_dev en général.

TASK_TYPE :
- edit     : rename, fix typo, format. Pas de logique nouvelle.
- refactor : extract function, restructure, style change. Code équivalent réorganisé.
- bug_fix  : un test échoue, un bug rapporté → corriger le code (pas le test).
- feature  : ajouter du code nouveau (fonction, classe, endpoint, test).
- explain  : question, lecture, analyse. Pas de modification de fichier.
- self_dev : l'utilisateur demande à Klody de modifier SON PROPRE code source
             (ajouter un outil à Klody, optimiser l'orchestrator, créer un nouveau
             prompt focalisé, intégrer une lib dans le repo klody-code-ai, etc.).
             Mots-clés : "améliore-toi", "ajoute-toi un outil", "modifie ton code",
             "optimise tes algorithmes", "intègre une nouvelle bibliothèque dans
             ton repo", "ajoute une fonctionnalité à Klody".

Exemples :
- "renomme `usr` en `user` dans app.py" → {"difficulty":"easy","task_type":"edit","reasoning":"rename localisé 1 fichier"}
- "ajoute une docstring à compute_area" → {"difficulty":"easy","task_type":"edit","reasoning":"docstring 1 fonction"}
- "le test test_calc.py échoue, corrige le code" → {"difficulty":"medium","task_type":"bug_fix","reasoning":"bug fix avec test existant"}
- "extrais ce code dupliqué en fonction" → {"difficulty":"medium","task_type":"refactor","reasoning":"refactor avec extraction"}
- "il y a une race condition dans worker.py" → {"difficulty":"hard","task_type":"bug_fix","reasoning":"debug async complexe"}
- "ajoute un endpoint FastAPI complet avec test" → {"difficulty":"hard","task_type":"feature","reasoning":"multi-fichier route+model+test"}
- "explique-moi comment fonctionne ce module" → {"difficulty":"easy","task_type":"explain","reasoning":"lecture sans modif"}
- "ajoute-toi un outil pour compter les lignes d'un fichier" → {"difficulty":"medium","task_type":"self_dev","reasoning":"nouveau tool dans le repo Klody"}
- "améliore tes performances de retrieval" → {"difficulty":"hard","task_type":"self_dev","reasoning":"optimisation interne Klody"}
"""

# Message de correction réinjecté entre deux essais quand la validation échoue.
_CORRECTION_PROMPT = (
    "Ta réponse précédente n'était pas un JSON valide conforme au schéma attendu. "
    "Réponds UNIQUEMENT par cet objet JSON, sans markdown ni texte autour : "
    '{"difficulty":"easy|medium|hard",'
    '"task_type":"edit|refactor|bug_fix|feature|explain|self_dev",'
    '"reasoning":"phrase courte"}'
)


class Router:
    """Router LLM léger qui classifie une demande coding."""

    def __init__(self, model: str | None = None):
        self.model = model or LLM_MODEL
        self.client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            timeout=LLM_HTTP_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )

    def classify(self, user_prompt: str) -> RoutingDecision:
        """Classifie un prompt en RoutingDecision.

        Conforme à docs/json-constraint-policy.md : validation Pydantic + RETRY borné
        (réinjection d'un message de correction) AVANT le fallback safe medium/explain.
        Une erreur de TRANSPORT (réseau/timeout) court-circuite le retry → fallback
        direct (réessayer le parse serait inutile).
        """
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _ROUTER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        last_raw = ""
        for attempt in range(_ROUTER_MAX_RETRIES + 1):
            raw = self._call_llm(messages)
            if raw is None:
                return self._fallback(reason="LLM error", raw=last_raw)
            last_raw = raw
            decision = self._parse_response(raw, user_prompt)
            if decision is not None:
                return decision
            if attempt < _ROUTER_MAX_RETRIES:
                logger.warning(
                    "Router: parse invalide (essai %d/%d) → relance avec correction",
                    attempt + 1, _ROUTER_MAX_RETRIES + 1,
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": _CORRECTION_PROMPT})

        logger.warning(
            "Router: parse invalide après %d essais → fallback", _ROUTER_MAX_RETRIES + 1
        )
        return self._fallback(
            reason=f"parse failed after {_ROUTER_MAX_RETRIES + 1} attempts", raw=last_raw
        )

    def _call_llm(self, messages: list[ChatCompletionMessageParam]) -> str | None:
        """Un appel de classification (sans retry). Retourne le content stripé, ou
        None sur erreur de transport (réseau, timeout…) — distinct d'un parse invalide."""
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=200,
                stream=False,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.error("Router LLM call failed: %s", exc)
            return None

    def _parse_response(self, raw: str, user_prompt: str) -> RoutingDecision | None:
        """Valide la réponse brute du LLM → RoutingDecision, ou None si invalide.

        Conforme à la politique JSON : NETTOYAGE (fences markdown, bloc <think> inline)
        PUIS validation Pydantic. Le nettoyage n'est jamais une garantie ; la garantie
        est la validation + le retry/fallback porté par classify(). Cette méthode ne
        relance PAS le LLM et n'applique PAS de fallback : elle retourne None pour
        signaler un échec de parse, à charge de l'appelant de décider retry vs fallback.
        """
        text = raw.strip()
        # Nettoyage (jamais une garantie) : <think>…</think> inline + fences markdown.
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
        match = re.search(r"\{[^{}]*\"difficulty\"[^{}]*\}", text, re.DOTALL)
        json_str = match.group(0) if match else text.strip()

        try:
            parsed = _RouterClassification.model_validate_json(json_str)
        except ValidationError:
            # Repli : certains modèles double-échappent le JSON (string contenant du JSON).
            try:
                parsed = _RouterClassification.model_validate(json.loads(json_str))
            except (ValidationError, json.JSONDecodeError, TypeError):
                logger.warning("Router: JSON invalide sur: %s", raw[:120])
                return None

        strategy = _decide_strategy(parsed.difficulty, parsed.task_type)
        return RoutingDecision(
            difficulty=parsed.difficulty,
            task_type=parsed.task_type,
            reasoning=parsed.reasoning[:200],
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

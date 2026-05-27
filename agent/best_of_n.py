"""Best-of-N conditionnel (Roadmap v2 #7).

Pour les tâches classifiées 'hard' par le Router, génère N candidats de réponse
LLM en variant la température, puis un LLM-as-judge sélectionne le meilleur.

Architecture :
1. generate_candidates → N appels LLM silencieux (températures 0.5 / 0.7 / 0.9)
2. rerank → 1 appel LLM avec rubrique de scoring → renvoie l'index gagnant
3. best → retourne le candidat gagnant (utilisé par l'orchestrator)

Coût : (N + 1) appels LLM au lieu de 1, déclenché UNIQUEMENT quand le router
dit `use_best_of_n=True` ET seulement à la première itération de la boucle
ReAct (où la stratégie initiale est décidée — les itérations suivantes sont
mécaniques).

Pourquoi LLM-as-judge plutôt qu'un reranker dédié :
- Pas de modèle supplémentaire à charger (~zéro mémoire ajoutée)
- Qwen3-Coder est très bon en évaluation de code
- Plus simple à itérer
- Pour v2+ : passer à un vrai reranker (Qwen3-Reranker-4B) si besoin
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    """Une proposition de réponse LLM (texte + éventuels tool calls)."""
    idx: int  # 0-indexé
    temperature: float
    content: str
    tool_calls: Optional[list[dict]]
    latency_s: float = 0.0

    def summary(self) -> str:
        """Représentation compacte pour le reranker."""
        bits: list[str] = []
        if self.content.strip():
            bits.append(f"Texte: {self.content.strip()[:300]}")
        if self.tool_calls:
            names = [tc["function"]["name"] for tc in self.tool_calls]
            bits.append(f"Tool calls: {', '.join(names)}")
            for tc in self.tool_calls[:3]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                    # Tronquer les valeurs longues (ex: content de write_file)
                    short = {k: (v[:120] + "…" if isinstance(v, str) and len(v) > 120 else v)
                             for k, v in args.items()}
                    bits.append(f"  • {tc['function']['name']}({short})")
                except (json.JSONDecodeError, TypeError):
                    bits.append(f"  • {tc['function']['name']}(<args invalides>)")
        if not bits:
            bits.append("(réponse vide)")
        return "\n".join(bits)


_RERANK_SYSTEM = """\
Tu évalues N propositions d'un agent de coding pour la même tâche.
Choisis la MEILLEURE selon ces critères, par ordre de priorité :

1. **Pertinence** : la proposition adresse-t-elle réellement la demande ?
2. **Robustesse** : lecture avant écriture, gestion des erreurs, pas d'effet de bord destructif.
3. **Concision** : pas de blabla, droit au but. Pas d'étapes inutiles.
4. **Utilisation des outils** : appelle les bons outils dans le bon ordre. Pour un refactor multi-fichier → find_references d'abord. Pour un bug → lire le test d'abord.
5. **Faisabilité** : tool calls valides, paramètres corrects.

Réponds UNIQUEMENT par un objet JSON :
{"choice": N, "reasoning": "phrase courte (max 100 chars)"}

où N est l'INDEX 1-based (1 = première proposition).
"""


def _format_candidates_for_rerank(user_prompt: str, cands: list[Candidate]) -> str:
    """Construit le user message du reranker."""
    lines = [f"## DEMANDE\n{user_prompt}\n", "## PROPOSITIONS\n"]
    for c in cands:
        lines.append(f"### [{c.idx + 1}]")
        lines.append(c.summary())
        lines.append("")
    return "\n".join(lines)


class BestOfN:
    """Génère N candidats puis sélectionne le meilleur via LLM-as-judge."""

    # Températures par défaut pour N=3 — on évite T=0.3 trop déterministe qui
    # pousse Qwen3-Coder à produire systématiquement des plans textuels sans
    # tool_call. T=0.5/0.8/1.0 maximise les chances qu'au moins UN candidat
    # émette un tool_call, ce qui déclenche l'override actionnable dans rerank().
    _TEMPERATURES = [0.5, 0.8, 1.0]

    def __init__(self, llm_client, n: int = 3, temperatures: Optional[list[float]] = None):
        self.llm = llm_client
        self.n = n
        self.temperatures = temperatures or self._TEMPERATURES[:n]
        # Si n > len(default), étendre en répétant 0.7
        while len(self.temperatures) < n:
            self.temperatures.append(0.7)

    def generate_candidates(self, messages: list[dict], tools: Optional[list[dict]] = None) -> list[Candidate]:
        """Lance N appels LLM silencieux, retourne les candidats."""
        import time
        cands: list[Candidate] = []
        for i in range(self.n):
            t0 = time.perf_counter()
            try:
                content, tool_calls = self.llm.stream_chat(
                    messages,
                    tools=tools,
                    temperature=self.temperatures[i],
                    silent=True,
                )
            except Exception as exc:
                logger.warning("Candidate %d failed: %s", i, exc)
                content, tool_calls = "", None
            elapsed = time.perf_counter() - t0
            cands.append(Candidate(
                idx=i,
                temperature=self.temperatures[i],
                content=content or "",
                tool_calls=tool_calls,
                latency_s=round(elapsed, 2),
            ))
        return cands

    def rerank(self, candidates: list[Candidate], user_prompt: str) -> tuple[int, str]:
        """Retourne (index_gagnant, reasoning).

        Stratégie en 2 temps :
        1. **Override objectif** : si au moins UN candidat a des tool_calls,
           on le préfère systématiquement (le plus rapide d'entre eux).
           Évite le piège "plan textuel sans action" qu'un LLM-judge favorise
           parfois en hard/feature avec température basse.
        2. **Fallback LLM-as-judge** : si aucun candidat n'a de tool_calls
           (réponse pure texte attendue), on utilise le judge classique.

        En cas de réponse invalide du reranker, fallback sur le candidat 0.
        """
        if not candidates:
            return 0, "no candidates"
        if len(candidates) == 1:
            return 0, "single candidate"

        # 1) Override : un candidat actionnable bat systématiquement un candidat textuel
        actionable = [c for c in candidates if c.tool_calls]
        if actionable:
            winner = min(actionable, key=lambda c: c.latency_s)
            n_act = len(actionable)
            if n_act == len(candidates):
                reason = f"action override: tous actionnables, choisi le plus rapide ({winner.latency_s}s)"
            else:
                reason = f"action override: {n_act}/{len(candidates)} avec tool_calls, évite le plan textuel"
            logger.info("[BoN] %s → idx=%d", reason, winner.idx)
            return winner.idx, reason

        # 2) Aucun candidat actionnable → LLM-as-judge classique
        user_msg = _format_candidates_for_rerank(user_prompt, candidates)
        messages = [
            {"role": "system", "content": _RERANK_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        try:
            content, _ = self.llm.stream_chat(
                messages,
                tools=None,
                temperature=0.0,
                silent=True,
            )
        except Exception as exc:
            logger.warning("Rerank LLM failed: %s", exc)
            return 0, f"rerank error: {exc}"

        return self._parse_rerank_response(content, n=len(candidates))

    @staticmethod
    def _parse_rerank_response(raw: str, n: int) -> tuple[int, str]:
        """Parse la réponse du reranker. Robuste à divers formats."""
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)

        # 1) Essai JSON
        match = re.search(r'\{[^{}]*"choice"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                choice = int(data.get("choice", 0))
                reasoning = str(data.get("reasoning", ""))[:200]
                if 1 <= choice <= n:
                    return choice - 1, reasoning
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # 2) Fallback : chercher un nombre isolé
        num_match = re.search(r"\b([1-9])\b", text)
        if num_match:
            choice = int(num_match.group(1))
            if 1 <= choice <= n:
                return choice - 1, f"fallback: number {choice} extracted"

        return 0, "fallback: unable to parse rerank response"

    def best(self, messages: list[dict], tools: Optional[list[dict]], user_prompt: str) -> tuple[Candidate, list[Candidate], str]:
        """Pipeline complet : generate → rerank → retourne le gagnant.

        Returns:
            (winner, all_candidates, reasoning)
        """
        cands = self.generate_candidates(messages, tools)
        winner_idx, reasoning = self.rerank(cands, user_prompt)
        return cands[winner_idx], cands, reasoning

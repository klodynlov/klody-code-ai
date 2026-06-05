"""Routeur de skills sémantique OPTIONNEL (couche A) — Klody.

Troisième niveau de routage, EN PLUS de `select_skills()` (IDF déterministe,
hors-ligne) qui reste le défaut. Ce module n'est PAS câblé par défaut : il faut
l'activer explicitement (cf. agent/orchestrator.py, flag SKILLS_ROUTER_ENABLED).

Dégradation gracieuse, jamais bloquante :
  1. EMBEDDINGS (Ollama natif /api/embed, modèle bge-m3 déjà présent localement
     — cf. tools/code_search.py) → pré-filtre des skills couche A par cosinus.
  2. JUGE LLM (endpoint principal, config.LLM_BASE_URL/LLM_MODEL) → tranche parmi
     les candidats pré-filtrés. Désactivable (use_llm_judge=False).
  3. FALLBACK = on RÉUTILISE tools.skills.select_skills (l'IDF maison), JAMAIS un
     keyword bis. Toute panne embeddings/LLM/timeout retombe ici sans exception.

Contrats respectés vs tools/skills.py (vérifiés contre le code réel) :
  - Entrée = skills couche A : dict {name, slug, description, content, updated}.
  - Chargement = tools.skills.load_skills() (aucun argument).
  - Skills permanents (slug utilisateur_* / conventions_*) = TOUJOURS injectés en
    tête, jamais filtrés, AVANT tout filtre embeddings/LLM (comme select_skills).
  - select_skills(skills, query, k) : on lui passe TOUTE la liste (il re-partitionne
    always/howto et renvoie always+picked) — signature réelle vérifiée.
  - Rendu = tools.skills.format_skills_for_prompt (section « ## Compétences acquises »).
"""

from __future__ import annotations

import json
import logging
import math
import re

import httpx
from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
)

from tools.skills import (
    _ALWAYS_PREFIXES,
    load_skills,
    select_skills,
)

logger = logging.getLogger(__name__)

# Embeddings : Ollama en NATIF (cf. tools/code_search.py:22-23), pas /v1/embeddings.
# bge-m3 est le modèle réellement disponible localement ; nomic-embed-text n'est
# PAS garanti pullé. Surchargeable à l'instanciation.
_DEFAULT_EMBED_URL = "http://localhost:11434/api/embed"
_DEFAULT_EMBED_MODEL = "bge-m3"

# Termes qui « autorisent » un skill de distillation (anti-collision routeur).
_DISTILL_GATE = ("livre", "livres", "auteur", "ouvrage", "ouvrages", "distill", "fusionn")


# --------------------------------------------------------------------------- #
# Similarité cosinus (sans numpy, comme tools/code_search.py)                  #
# --------------------------------------------------------------------------- #


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosinus entre deux vecteurs ; 0.0 si l'un est vide ou de taille ≠."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# --------------------------------------------------------------------------- #
# Routeur sémantique optionnel                                                 #
# --------------------------------------------------------------------------- #


class SkillRouter:
    """Routeur sémantique des skills couche A, avec dégradation vers select_skills.

    Usage (opt-in) :
        router = SkillRouter()
        skills = router.select(user_request, k=5)          # list[dict] couche A
        section = router.render(skills)                     # str injectable

    `select()` renvoie TOUJOURS une liste cohérente avec select_skills (mêmes
    dict, mêmes skills permanents en tête) : si embeddings ET juge échouent, le
    résultat est EXACTEMENT celui de select_skills (aucune régression possible).

    IMPORTANT latence : instancier UNE fois (cache _desc_vecs en mémoire d'instance)
    et réutiliser ; ne PAS recréer un SkillRouter à chaque _inject_system_prompt.
    """

    def __init__(
        self,
        *,
        use_llm_judge: bool = True,
        embed_url: str = _DEFAULT_EMBED_URL,
        embed_model: str = _DEFAULT_EMBED_MODEL,
        prefilter_top_k: int = 6,
        prefilter_threshold: float = 0.35,
        timeout: float = 20.0,
    ) -> None:
        self.use_llm_judge = use_llm_judge
        self.embed_url = embed_url
        self.embed_model = embed_model
        self.prefilter_top_k = prefilter_top_k
        self.prefilter_threshold = prefilter_threshold
        self.timeout = timeout
        # Cache embeddings des descriptions, par slug (lazy, en mémoire d'instance).
        self._desc_vecs: dict[str, list[float]] = {}

    # -- API publique ------------------------------------------------------- #

    def select(self, user_request: str, k: int = 5) -> list[dict]:
        """Sélectionne les skills couche A à injecter.

        Garantit en tête les skills permanents (utilisateur_/conventions_).
        Dégrade : embeddings → juge LLM → select_skills (IDF). Jamais d'exception
        propagée : tout échec retombe sur select_skills.
        """
        skills = load_skills()
        if not skills:
            return []

        always = [s for s in skills if self._is_always(s)]
        howto = [s for s in skills if not self._is_always(s)]

        if not (user_request or "").strip() or not howto:
            return always

        # Niveau 1 : pré-filtre embeddings (peut renvoyer [] si endpoint KO).
        ranked = self._rank_by_embedding(user_request, howto)

        if not ranked:
            # Embeddings indisponibles → on délègue tout à l'IDF déterministe.
            logger.info("skill_router: embeddings KO → fallback select_skills (IDF)")
            return select_skills(skills, user_request, k=k)

        candidates = [s for score, s in ranked if score >= self.prefilter_threshold]
        candidates = candidates[: self.prefilter_top_k] or [ranked[0][1]]

        # Anti-collision distiller_* : on retire les skills de distillation si la
        # requête n'évoque ni livre ni distillation (faux positif sémantique).
        candidates = self._gate_distill(user_request, candidates)
        if not candidates:
            logger.info("skill_router: aucun candidat après garde-fou → select_skills")
            return select_skills(skills, user_request, k=k)

        chosen = candidates
        if self.use_llm_judge:
            judged = self._judge(user_request, candidates, k)
            if judged:  # le juge a tranché ; sinon on garde le rang embeddings
                chosen = judged

        chosen = chosen[:k]
        logger.info(
            "skill_router.select: query=%r\\n  ranked=%s\\n  candidates=%s\\n  chosen=%s",
            user_request,
            [(round(sc, 3), s.get("slug")) for sc, s in ranked[: self.prefilter_top_k]],
            [s.get("slug") for s in candidates],
            [s.get("slug") for s in chosen],
        )
        return always + chosen

    def render(self, skills: list[dict]) -> str:
        """Réutilise le rendu maison (section « ## Compétences acquises »)."""
        from tools.skills import format_skills_for_prompt

        return format_skills_for_prompt(skills)

    # -- niveau 1 : embeddings --------------------------------------------- #

    def _rank_by_embedding(
        self, user_request: str, howto: list[dict]
    ) -> list[tuple[float, dict]]:
        """[(score, skill), ...] décroissant. [] si embeddings indisponibles."""
        q = self._embed_one(user_request)
        if not q:
            return []
        scored: list[tuple[float, dict]] = []
        for s in howto:
            vec = self._desc_vec(s)
            if not vec:
                return []  # un seul échec d'embed description → on dégrade en bloc
            scored.append((_cosine(q, vec), s))
        scored.sort(key=lambda x: -x[0])
        return scored

    def _desc_vec(self, skill: dict) -> list[float]:
        """Embedding (caché) de la description d'un skill, clé = slug."""
        slug = str(skill.get("slug", ""))
        if slug in self._desc_vecs:
            return self._desc_vecs[slug]
        text = f"{skill.get('name', '')}. {skill.get('description', '')}".strip()
        vec = self._embed_one(text)
        if vec:
            self._desc_vecs[slug] = vec
        return vec

    def _embed_one(self, text: str) -> list[float]:
        """Appelle Ollama /api/embed (natif). [] si échec (jamais d'exception)."""
        if not (text or "").strip():
            return []
        try:
            resp = httpx.post(
                self.embed_url,
                json={"model": self.embed_model, "input": [text]},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            vecs = resp.json().get("embeddings", [])
            return [float(x) for x in vecs[0]] if vecs and vecs[0] else []
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            logger.debug("skill_router embed KO: %s", exc)
            return []

    # -- niveau 2 : juge LLM ----------------------------------------------- #

    def _judge(self, user_request: str, candidates: list[dict], k: int) -> list[dict]:
        """Demande au LLM principal de trancher. [] si échec → on garde le rang."""
        by_slug = {str(s.get("slug", "")): s for s in candidates}
        menu = "\\n".join(
            f"- {s.get('slug')}: {s.get('description', '')}" for s in candidates
        )
        system = (
            "Tu es un routeur de compétences. On te donne un menu (slug + "
            "description) et une requête utilisateur. Renvoie UNIQUEMENT un tableau "
            f"JSON des slugs réellement pertinents, du plus au moins pertinent, au "
            f"plus {k}. Si aucun ne s'applique, renvoie []. Règle stricte : ne "
            "choisis un skill 'distiller_*' QUE si la requête mentionne explicitement "
            "un livre, un auteur ou une distillation. Aucun texte hors du JSON."
        )
        user = f"MENU:\\n{menu}\\n\\nREQUÊTE:\\n{user_request}\\n\\nJSON:"
        content = self._chat(system, user)
        slugs = _parse_slug_list(content, valid=set(by_slug))
        return [by_slug[s] for s in slugs if s in by_slug]

    def _chat(self, system: str, user: str) -> str:
        """Appel chat au LLM principal (config.LLM_*). '' si échec.

        LLM_BASE_URL finit par /v1 (Ollama comme MLX, cf. config.py:28) → on
        ajoute /chat/completions, comme le fait le SDK OpenAI dans agent/llm.py.
        """
        try:
            resp = httpx.post(
                f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
                json={
                    "model": LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 120,
                    "temperature": 0.0,
                },
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            logger.debug("skill_router judge KO: %s", exc)
            return ""

    # -- garde-fous --------------------------------------------------------- #

    @staticmethod
    def _is_always(skill: dict) -> bool:
        return str(skill.get("slug", "")).startswith(_ALWAYS_PREFIXES)

    @staticmethod
    def _gate_distill(user_request: str, candidates: list[dict]) -> list[dict]:
        """Retire les skills distiller_* si la requête n'évoque pas livre/distill."""
        low = (user_request or "").lower()
        if any(g in low for g in _DISTILL_GATE):
            return candidates
        return [
            s for s in candidates
            if not str(s.get("slug", "")).startswith("distiller")
        ]


# --------------------------------------------------------------------------- #
# Parsing de la réponse du juge                                               #
# --------------------------------------------------------------------------- #


def _parse_slug_list(content: str, valid: set[str]) -> list[str]:
    """Extrait une liste JSON de slugs valides de la réponse LLM (robuste)."""
    content = (content or "").strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
    m = re.search(r"\\[.*?\\]", content, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip() in valid]
        except (ValueError, TypeError):
            pass
    return [s for s in valid if s in content]


# --------------------------------------------------------------------------- #
# CÂBLAGE (opt-in) — à appliquer dans agent/orchestrator.py et config.py      #
# --------------------------------------------------------------------------- #
#
# config.py (défaut OFF = offline-first préservé) :
#     SKILLS_ROUTER_ENABLED: bool = os.getenv("SKILLS_ROUTER_ENABLED", "0") == "1"
#     SKILLS_ROUTER_JUDGE:   bool = os.getenv("SKILLS_ROUTER_JUDGE", "1") == "1"
#
# agent/orchestrator.py — UN seul point, dans _inject_system_prompt (≈ ligne 530),
# remplacer `skills = select_skills(load_skills(), query)` par :
#
#     import config
#     if getattr(config, "SKILLS_ROUTER_ENABLED", False):
#         skills = self._get_skill_router().select(query, k=5)
#     else:
#         skills = select_skills(load_skills(), query)
#
# et ajouter sur l'orchestrateur un singleton lazy (CACHE _desc_vecs préservé
# entre tours ReAct — point critique de latence) :
#
#     def _get_skill_router(self):
#         if getattr(self, "_skill_router", None) is None:
#             import config
#             from tools.skill_router import SkillRouter
#             self._skill_router = SkillRouter(
#                 use_llm_judge=getattr(config, "SKILLS_ROUTER_JUDGE", True)
#             )
#         return self._skill_router
#
# Le reste de _inject_system_prompt est INCHANGÉ : self._injected_skill_slugs et
# format_skills_for_prompt(skills) fonctionnent tels quels car select() renvoie
# les MÊMES dict couche A avec les permanents en tête → chip UI (api/server.py:820)
# et hot-swap du system message restent corrects. La branche _code_model_active
# (orchestrator.py:520) n'injecte aucun skill : ne pas la toucher.

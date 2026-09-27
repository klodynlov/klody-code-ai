"""
Profilage utilisateur intelligent.

Analyse chaque requête pour construire un profil cumulatif :
- Technologies utilisées (compteur par tech)
- Catégories de requêtes (web, api, data, devops, ai, project, debug)
- Patterns récurrents (séquences de catégories fréquentes)
- Suggestions proactives basées sur le profil + skills + mémoire

Persisté dans MEMORY_DIR/user_profile.json entre les sessions.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from config import MEMORY_DIR

logger = logging.getLogger(__name__)

_PROFILE_FILE = MEMORY_DIR / "user_profile.json"

# ── Détection de technologies ────────────────────────────────────────────────

_TECH_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Python", re.compile(r"\b(python|pip|pytest|pyproject|venv|poetry|uv)\b", re.I)),
    ("JavaScript", re.compile(r"\b(javascript|js|node|npm|yarn|pnpm|bun)\b", re.I)),
    ("TypeScript", re.compile(r"\b(typescript|tsx?)\b", re.I)),
    ("HTML/CSS", re.compile(r"\b(html|css|scss|sass|tailwind|bootstrap)\b", re.I)),
    ("React", re.compile(r"\b(react|jsx|next\.?js|remix)\b", re.I)),
    ("Vue", re.compile(r"\b(vue|nuxt|vuetify)\b", re.I)),
    ("FastAPI", re.compile(r"\b(fastapi|uvicorn|starlette)\b", re.I)),
    ("Django", re.compile(r"\b(django)\b", re.I)),
    ("Flask", re.compile(r"\b(flask)\b", re.I)),
    ("Symfony", re.compile(r"\b(symfony|php|composer|twig)\b", re.I)),
    ("SQL", re.compile(r"\b(sql|postgres|mysql|sqlite|supabase|prisma)\b", re.I)),
    ("MongoDB", re.compile(r"\b(mongo|mongoose)\b", re.I)),
    ("Redis", re.compile(r"\b(redis|celery)\b", re.I)),
    ("Docker", re.compile(r"\b(docker|compose|kubernetes|k8s)\b", re.I)),
    ("Git", re.compile(r"\b(git|github|gitlab|commit|branch|merge|pr|pull.?request)\b", re.I)),
    ("CI/CD", re.compile(r"\b(ci/cd|github.?actions|gitlab.?ci|jenkins|deploy)\b", re.I)),
    ("AI/ML", re.compile(r"\b(ollama|llm|gpt|claude|mlx|pytorch|tensorflow|hugging.?face|langchain|rag)\b", re.I)),
    ("Swift", re.compile(r"\b(swift|swiftui|xcode|ios)\b", re.I)),
    ("Rust", re.compile(r"\b(rust|cargo|tokio)\b", re.I)),
    ("Go", re.compile(r"\b(golang|go\s+mod)\b", re.I)),
    ("Tauri", re.compile(r"\b(tauri)\b", re.I)),
    ("Electron", re.compile(r"\b(electron)\b", re.I)),
]

# ── Catégorisation de requêtes ───────────────────────────────────────────────

_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("web", re.compile(r"\b(page|site|html|css|js|responsive|formulaire|bouton|navbar|landing|frontend|ui|ux|design|animation|canvas)\b", re.I)),
    ("api", re.compile(r"\b(api|endpoint|route|rest|graphql|backend|serveur|server|fastapi|express|auth|jwt|token|cors)\b", re.I)),
    ("data", re.compile(r"\b(base.?de.?données|database|sql|requête|table|migration|schéma|csv|json|excel|pandas|data)\b", re.I)),
    ("devops", re.compile(r"\b(docker|deploy|ci|cd|pipeline|k8s|kubernetes|nginx|terraform|ansible|monitoring)\b", re.I)),
    ("ai", re.compile(r"\b(ia|ai|llm|model|prompt|rag|embedding|fine.?tun|train|inference|agent|chatbot)\b", re.I)),
    ("project", re.compile(r"\b(projet|créer|crée|init|structure|template|scaffold|boilerplate|architecture)\b", re.I)),
    ("debug", re.compile(r"\b(bug|erreur|error|fix|debug|crash|plantage|problème|marche.?pas|ne.?fonctionne|traceback|exception)\b", re.I)),
    ("preview", re.compile(r"\b(aperçu|preview|voir|afficher|navigateur|browser|rendu|visuel)\b", re.I)),
    ("learn", re.compile(r"\b(apprendre|comprendre|expliqu|comment|pourquoi|différence|c.?est.?quoi|tutoriel|documentation)\b", re.I)),
    ("refactor", re.compile(r"\b(refactor|optimis|améliore|nettoie|clean|restructur|simplifie)\b", re.I)),
]


class UserProfiler:
    """Profilage cumulatif de l'utilisateur à travers les sessions."""

    def __init__(self) -> None:
        self.tech_usage: dict[str, int] = {}
        self.request_categories: dict[str, int] = {}
        self.recent_categories: list[str] = []  # 20 dernières catégories (séquence)
        self.session_count: int = 0
        self.total_requests: int = 0
        self.top_skills_used: dict[str, int] = {}  # skills demandées par l'user
        self.last_updated: str = ""
        self._load()

    # ── API publique ─────────────────────────────────────────────────────

    def track_request(self, user_input: str) -> dict:
        """Analyse une requête et met à jour le profil.

        Retourne un dict {techs: [...], categories: [...]} pour usage immédiat.
        """
        techs = self._detect_techs(user_input)
        categories = self._detect_categories(user_input)

        for tech in techs:
            self.tech_usage[tech] = self.tech_usage.get(tech, 0) + 1

        for cat in categories:
            self.request_categories[cat] = self.request_categories.get(cat, 0) + 1

        if categories:
            self.recent_categories.extend(categories)
            self.recent_categories = self.recent_categories[-20:]

        self.total_requests += 1
        self.last_updated = datetime.now().isoformat()
        self._save()

        return {"techs": techs, "categories": categories}

    def track_tool_usage(self, tool_name: str) -> None:
        """Compte l'usage des outils pour détecter les patterns."""
        self.top_skills_used[tool_name] = self.top_skills_used.get(tool_name, 0) + 1

    def increment_session(self) -> None:
        self.session_count += 1
        self._save()

    def get_suggestions(self, user_input: str, available_skills: list[dict]) -> list[str]:
        """Génère des suggestions proactives basées sur le profil et la requête.

        Retourne une liste de suggestions textuelles (0 à 3 max).
        """
        suggestions: list[str] = []
        analysis = {"techs": self._detect_techs(user_input), "categories": self._detect_categories(user_input)}

        # 1. Suggérer une skill pertinente
        skill_match = self._match_skill(user_input, analysis, available_skills)
        if skill_match:
            suggestions.append(f"💡 Compétence disponible : **{skill_match['name']}** — {skill_match['description'][:80]}")

        # 2. Suggérer LibraryBrain si sujet technique détecté
        if analysis["techs"] and not any(c in ("learn",) for c in analysis["categories"]):
            top_tech = analysis["techs"][0]
            if self.tech_usage.get(top_tech, 0) <= 2:
                suggestions.append(f"📚 LibraryBrain peut enrichir tes connaissances sur **{top_tech}** → demande-moi « apprends {top_tech} depuis les livres »")

        # 3. Détecter un pattern récurrent et suggérer une automatisation
        pattern = self._detect_recurring_pattern()
        if pattern:
            suggestions.append(f"🔄 Pattern récurrent détecté : **{pattern}** — je peux créer une compétence pour accélérer")

        # 4. Suggérer preview pour les requêtes web
        if (
            "web" in analysis["categories"]
            and "preview" not in analysis["categories"]
            and self.request_categories.get("web", 0) >= 3
        ):
            suggestions.append("👁 Je peux générer un aperçu live dans le navigateur — dis « avec aperçu »")

        return suggestions[:3]

    def get_profile_for_prompt(self) -> str:
        """Formate le profil utilisateur pour injection dans le system prompt.

        ⚠️ Aucun COMPTEUR ici : ce texte vit dans le prompt système, et le cache
        de préfixe de mlx_lm (ArraysCache du MoE, non rognable) ne réutilise
        qu'un préfixe EXACT. « Requêtes : N », incrémenté à chaque message, et
        les « (N×) » invalidaient tout le préfixe — schémas d'outils compris —
        au premier appel de CHAQUE message. Mesuré le 2026-09-27 sur le
        gateway : dernier token du système changé ⇒ cached=0, 11,2 s ; rejeu
        exact ⇒ cached=11 517/11 518, 0,55 s. Rejoué sur 218 paires de messages
        réels consécutifs : profil identique 0 % avant ce correctif. Seul le
        CLASSEMENT est dit — il ne bouge que lorsqu'un rang s'inverse.
        """
        if self.total_requests < 3:
            return ""

        lines = ["\n\n## Profil utilisateur (appris automatiquement)\n"]

        # Top techs
        top_techs = sorted(self.tech_usage.items(), key=lambda x: -x[1])[:8]
        if top_techs:
            tech_str = ", ".join(t for t, _c in top_techs)
            lines.append(f"**Stack préférée** : {tech_str}")

        # Top catégories
        top_cats = sorted(self.request_categories.items(), key=lambda x: -x[1])[:5]
        if top_cats:
            _CAT_LABELS = {
                "web": "Développement web", "api": "APIs & backend",
                "data": "Données & BDD", "devops": "DevOps & infra",
                "ai": "IA & machine learning", "project": "Gestion de projet",
                "debug": "Débogage", "preview": "Aperçus visuels",
                "learn": "Apprentissage", "refactor": "Refactoring",
            }
            cats_str = ", ".join(_CAT_LABELS.get(c, c) for c, _n in top_cats)
            lines.append(f"**Activités principales** : {cats_str}")

        # Patterns
        pattern = self._detect_recurring_pattern(avec_compte=False)
        if pattern:
            lines.append(f"**Pattern récurrent** : {pattern}")

        lines.append("")
        lines.append(
            "_Utilise ce profil pour personnaliser tes réponses : "
            "propose des solutions dans la stack préférée, "
            "anticipe les besoins récurrents, sois force de proposition._"
        )

        return "\n".join(lines)

    def get_display_summary(self) -> dict:
        """Retourne un résumé du profil pour affichage CLI."""
        return {
            "sessions": self.session_count,
            "requêtes": self.total_requests,
            "top_techs": sorted(self.tech_usage.items(), key=lambda x: -x[1])[:10],
            "top_categories": sorted(self.request_categories.items(), key=lambda x: -x[1])[:8],
            "top_tools": sorted(self.top_skills_used.items(), key=lambda x: -x[1])[:8],
            "pattern": self._detect_recurring_pattern(),
        }

    # ── Détection interne ────────────────────────────────────────────────

    def _detect_techs(self, text: str) -> list[str]:
        found = []
        for name, pattern in _TECH_PATTERNS:
            if pattern.search(text):
                found.append(name)
        return found

    def _detect_categories(self, text: str) -> list[str]:
        found = []
        for name, pattern in _CATEGORY_PATTERNS:
            if pattern.search(text):
                found.append(name)
        return found or ["general"]

    def _match_skill(self, user_input: str, analysis: dict, skills: list[dict]) -> dict | None:
        """Trouve une skill pertinente pour la requête actuelle."""
        if not skills:
            return None

        input_lower = user_input.lower()
        techs_lower = {t.lower() for t in analysis.get("techs", [])}

        best: dict | None = None
        best_score = 0

        for skill in skills:
            score = 0
            name_lower = skill.get("name", "").lower()
            desc_lower = skill.get("description", "").lower()
            content_lower = skill.get("content", "").lower()

            # Match par nom dans la requête
            for word in name_lower.split():
                if len(word) > 3 and word in input_lower:
                    score += 3

            # Match par tech
            for tech in techs_lower:
                if tech in name_lower or tech in desc_lower:
                    score += 2

            # Match par mots-clés
            for word in input_lower.split():
                if len(word) > 4 and (word in desc_lower or word in content_lower):
                    score += 1

            if score > best_score and score >= 3:
                best_score = score
                best = skill

        return best

    def _detect_recurring_pattern(self, avec_compte: bool = True) -> str | None:
        """Détecte les séquences de catégories récurrentes.

        `avec_compte=False` pour le prompt système : le « (N× détecté) » change à
        chaque occurrence et casserait le cache de préfixe (cf.
        `get_profile_for_prompt`). L'affichage CLI garde le compte.
        """
        if len(self.recent_categories) < 6:
            return None

        _CAT_LABELS = {
            "web": "web", "api": "API", "debug": "debug",
            "project": "création projet", "preview": "aperçu",
            "data": "données", "ai": "IA", "refactor": "refactoring",
        }

        # Compter les paires consécutives
        pairs: dict[str, int] = {}
        for i in range(len(self.recent_categories) - 1):
            a, b = self.recent_categories[i], self.recent_categories[i + 1]
            if a != b and a != "general" and b != "general":
                key = f"{a} → {b}"
                pairs[key] = pairs.get(key, 0) + 1

        if not pairs:
            return None

        top_pair, count = max(pairs.items(), key=lambda x: x[1])
        if count >= 3:
            parts = top_pair.split(" → ")
            a_label = _CAT_LABELS.get(parts[0], parts[0])
            b_label = _CAT_LABELS.get(parts[1], parts[1])
            motif = f"{a_label} → {b_label}"
            return f"{motif} ({count}× détecté)" if avec_compte else motif

        return None

    # ── Persistance ──────────────────────────────────────────────────────

    def _save(self) -> None:
        data = {
            "tech_usage": self.tech_usage,
            "request_categories": self.request_categories,
            "recent_categories": self.recent_categories,
            "session_count": self.session_count,
            "total_requests": self.total_requests,
            "top_skills_used": self.top_skills_used,
            "last_updated": self.last_updated,
        }
        try:
            _PROFILE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.error("[Profiler] Erreur sauvegarde: %s", e)

    def _load(self) -> None:
        if not _PROFILE_FILE.exists():
            return
        try:
            data = json.loads(_PROFILE_FILE.read_text(encoding="utf-8"))
            self.tech_usage = data.get("tech_usage", {})
            self.request_categories = data.get("request_categories", {})
            self.recent_categories = data.get("recent_categories", [])
            self.session_count = data.get("session_count", 0)
            self.total_requests = data.get("total_requests", 0)
            self.top_skills_used = data.get("top_skills_used", {})
            self.last_updated = data.get("last_updated", "")
        except Exception as e:
            logger.error("[Profiler] Erreur chargement: %s", e)


# Singleton
_instance: UserProfiler | None = None


def get_profiler() -> UserProfiler:
    global _instance
    if _instance is None:
        _instance = UserProfiler()
    return _instance

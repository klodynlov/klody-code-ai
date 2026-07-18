"""Tests pour tools/skill_router.py — routeur de skills sémantique optionnel.

Tout est déterministe et hors-ligne : les appels réseau (embeddings Ollama,
juge LLM) sont monkeypatchés. On vérifie surtout l'invariant central : `select()`
ne lève JAMAIS et, dès qu'un maillon sémantique manque, retombe EXACTEMENT sur
`select_skills` (l'IDF déterministe) — zéro régression possible.
"""

import pytest
from tools.skill_router import SkillRouter, _cosine, _parse_slug_list
from tools.skills import select_skills

# ── Fixtures ────────────────────────────────────────────────────────────────

SKILLS = [
    {"name": "Profil", "slug": "utilisateur_profil",
     "description": "contexte permanent sur l'utilisateur", "content": "c", "updated": ""},
    {"name": "Conventions", "slug": "conventions_repo",
     "description": "règles et conventions du dépôt", "content": "c", "updated": ""},
    {"name": "Next.js", "slug": "nextjs",
     "description": "développement web avec Next.js et React", "content": "c", "updated": ""},
    {"name": "Distiller plusieurs livres", "slug": "distiller_plusieurs_livres",
     "description": "fusionner plusieurs livres en un skill", "content": "c", "updated": ""},
    {"name": "MLX", "slug": "mlx",
     "description": "inférence locale MLX sur Apple Silicon", "content": "c", "updated": ""},
]


@pytest.fixture
def patched_skills(monkeypatch):
    """load_skills() (lié dans le module skill_router) renvoie le jeu de test."""
    monkeypatch.setattr("tools.skill_router.load_skills", lambda: list(SKILLS))
    return SKILLS


def _slugs(skills):
    return [s["slug"] for s in skills]


# ── _cosine ─────────────────────────────────────────────────────────────────

class TestCosine:
    def test_identique(self):
        assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_vecteur_vide_ou_taille_differente(self):
        assert _cosine([], [1.0]) == 0.0
        assert _cosine([1.0, 2.0], [1.0]) == 0.0


# ── garde-fous ──────────────────────────────────────────────────────────────

class TestGardeFous:
    def test_is_always(self):
        assert SkillRouter._is_always({"slug": "utilisateur_profil"})
        assert SkillRouter._is_always({"slug": "conventions_repo"})
        assert not SkillRouter._is_always({"slug": "nextjs"})

    def test_gate_distill_retire_si_pas_de_terme_livre(self):
        cands = [SKILLS[3], SKILLS[2]]  # distiller_plusieurs_livres, nextjs
        out = SkillRouter._gate_distill("optimise mon algorithme de tri", cands)
        assert _slugs(out) == ["nextjs"]

    def test_gate_distill_conserve_si_terme_livre_present(self):
        cands = [SKILLS[3], SKILLS[2]]
        out = SkillRouter._gate_distill("fusionne ces livres en un skill", cands)
        assert _slugs(out) == ["distiller_plusieurs_livres", "nextjs"]


# ── _parse_slug_list ────────────────────────────────────────────────────────

_PARSE_VALID = {"nextjs", "mlx"}


class TestParseSlugList:
    def test_tableau_json_simple(self):
        assert _parse_slug_list('["nextjs", "mlx"]', _PARSE_VALID) == ["nextjs", "mlx"]

    def test_code_fence_et_texte_autour(self):
        out = _parse_slug_list('```json\n["mlx"]\n```', _PARSE_VALID)
        assert out == ["mlx"]

    def test_filtre_les_slugs_inconnus(self):
        assert _parse_slug_list('["nextjs", "inexistant"]', _PARSE_VALID) == ["nextjs"]

    def test_fallback_substring_si_pas_de_json(self):
        assert _parse_slug_list("je choisis mlx", _PARSE_VALID) == ["mlx"]

    def test_vide_si_rien(self):
        assert _parse_slug_list("aucun", _PARSE_VALID) == []


# ── select() : dégradation gracieuse (cœur) ─────────────────────────────────

class TestSelectDegradation:
    def test_embeddings_ko_retombe_exactement_sur_select_skills(
        self, patched_skills, monkeypatch
    ):
        # Embeddings indisponibles : tout embed renvoie [].
        monkeypatch.setattr(SkillRouter, "_embed_one", lambda self, text: [])
        router = SkillRouter(use_llm_judge=False)
        q = "développement next.js et react"
        got = router.select(q, k=5)
        expected = select_skills(list(SKILLS), q, k=5)
        assert _slugs(got) == _slugs(expected)

    def test_embeddings_qui_levent_sont_captes_et_degradent(
        self, patched_skills, monkeypatch
    ):
        # Le moteur d'embeddings lève : on exerce le VRAI chemin _embed_one →
        # _rank_by_embedding → fallback, sans bypass. select() ne doit pas propager.
        # (Depuis 2026-07-18 les embeddings sont in-process : plus de panne réseau,
        # mais une exception du moteur doit rester tout aussi inoffensive.)
        from tools import embeddings

        def boom(*args, **kwargs):
            raise RuntimeError("moteur d'embeddings en vrac")
        monkeypatch.setattr(embeddings, "embed_one", boom)
        router = SkillRouter(use_llm_judge=True)
        q = "développement next.js et react"
        got = router.select(q, k=5)
        assert _slugs(got) == _slugs(select_skills(list(SKILLS), q, k=5))

    def test_requete_vide_renvoie_uniquement_les_always(
        self, patched_skills, monkeypatch
    ):
        monkeypatch.setattr(SkillRouter, "_embed_one", lambda self, text: [])
        router = SkillRouter()
        got = router.select("", k=5)
        assert _slugs(got) == ["utilisateur_profil", "conventions_repo"]


# ── select() : chemin sémantique complet (embeddings + juge) ────────────────

class TestSelectAvecJuge:
    def test_juge_tranche_parmi_les_candidats(self, patched_skills, monkeypatch):
        # Rang embeddings contrôlé (nextjs > mlx), tous deux au-dessus du seuil.
        monkeypatch.setattr(
            SkillRouter, "_rank_by_embedding",
            lambda self, q, howto: [(0.9, SKILLS[2]), (0.8, SKILLS[4])],
        )
        monkeypatch.setattr(SkillRouter, "_chat", lambda self, s, u: '["mlx"]')
        router = SkillRouter(use_llm_judge=True)
        got = router.select("quelque chose", k=5)
        # always en tête, puis le choix du juge.
        assert _slugs(got) == ["utilisateur_profil", "conventions_repo", "mlx"]

    def test_juge_vide_garde_le_rang_embeddings(self, patched_skills, monkeypatch):
        monkeypatch.setattr(
            SkillRouter, "_rank_by_embedding",
            lambda self, q, howto: [(0.9, SKILLS[2]), (0.8, SKILLS[4])],
        )
        monkeypatch.setattr(SkillRouter, "_chat", lambda self, s, u: "[]")
        router = SkillRouter(use_llm_judge=True)
        got = router.select("quelque chose", k=5)
        assert _slugs(got) == ["utilisateur_profil", "conventions_repo", "nextjs", "mlx"]

    def test_juge_qui_leve_garde_le_rang_embeddings(self, patched_skills, monkeypatch):
        # Ollama UP (embeddings OK) mais LLM DOWN : _chat → httpx.post lève →
        # capté en interne → '' → [] → on garde le rang embeddings, sans propager.
        import httpx
        monkeypatch.setattr(
            SkillRouter, "_rank_by_embedding",
            lambda self, q, howto: [(0.9, SKILLS[2]), (0.8, SKILLS[4])],
        )

        def boom(*args, **kwargs):
            raise httpx.ConnectError("LLM indisponible")
        monkeypatch.setattr(httpx, "post", boom)
        router = SkillRouter(use_llm_judge=True)
        got = router.select("quelque chose", k=5)
        assert _slugs(got) == ["utilisateur_profil", "conventions_repo", "nextjs", "mlx"]


# ── flag opt-in ─────────────────────────────────────────────────────────────

def test_flag_off_par_defaut():
    import config
    assert config.SKILLS_ROUTER_ENABLED is False

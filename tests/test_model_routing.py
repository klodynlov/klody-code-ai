"""Routage modèle code : LLMClient.switch_to + Orchestrator._route_model.

Les tâches de code (edit/refactor/bug_fix/feature/self_dev) sont routées vers le
modèle coder dédié ; `explain` reste sur le généraliste. Voir config.CODE_MODEL.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.llm import LLMClient
from agent.orchestrator import Orchestrator, _skill_is_interactive

# ── LLMClient.switch_to ───────────────────────────────────────────────────────


class TestSwitchTo:
    def _client(self, model: str) -> LLMClient:
        c = LLMClient.__new__(LLMClient)  # sans __init__ (pas de client réseau)
        c.model = model
        c.client = object()  # sentinelle
        return c

    def test_bascule_change_modele_et_recree_client(self):
        c = self._client("general")
        old_client = c.client
        c.switch_to("coder", "http://localhost:8081/v1", "mlx")
        assert c.model == "coder"
        assert c.client is not old_client

    def test_no_op_si_meme_modele(self):
        c = self._client("coder")
        sentinel = object()
        c.client = sentinel
        c.switch_to("coder", "http://localhost:8081/v1", "mlx")
        assert c.client is sentinel  # client NON recréé


# ── Orchestrator._route_model ─────────────────────────────────────────────────


def _fake_orch(model: str) -> MagicMock:
    """Faux orchestrateur minimal : un .llm dont switch_to mute le modèle."""
    o = MagicMock()
    o.llm.model = model

    def _switch(m, b, k):
        o.llm.model = m

    o.llm.switch_to.side_effect = _switch
    return o


def _set_models(monkeypatch, *, code="coder-30b", general="general-35b"):
    monkeypatch.setattr("agent.orchestrator.CODE_MODEL", code)
    monkeypatch.setattr("agent.orchestrator.CODE_BASE_URL", "http://localhost:8081/v1")
    monkeypatch.setattr("agent.orchestrator.CODE_API_KEY", "mlx")
    monkeypatch.setattr("agent.orchestrator.LLM_MODEL", general)
    monkeypatch.setattr("agent.orchestrator.LLM_BASE_URL", "http://localhost:8080/v1")
    monkeypatch.setattr("agent.orchestrator.LLM_API_KEY", "mlx")


class TestRouteModel:
    def test_tache_code_va_au_coder(self, monkeypatch):
        _set_models(monkeypatch)
        o = _fake_orch("general-35b")
        Orchestrator._route_model(o, "feature")
        o.llm.switch_to.assert_called_once_with("coder-30b", "http://localhost:8081/v1", "mlx")
        assert o._code_model_active is True  # → prompt slim

    def test_bug_fix_et_refactor_aussi(self, monkeypatch):
        _set_models(monkeypatch)
        for tt in ("bug_fix", "refactor", "edit", "self_dev"):
            o = _fake_orch("general-35b")
            Orchestrator._route_model(o, tt)
            o.llm.switch_to.assert_called_once_with("coder-30b", "http://localhost:8081/v1", "mlx")

    def test_explain_revient_au_generaliste(self, monkeypatch):
        _set_models(monkeypatch)
        o = _fake_orch("coder-30b")  # on était sur le coder
        Orchestrator._route_model(o, "explain")
        o.llm.switch_to.assert_called_once_with("general-35b", "http://localhost:8080/v1", "mlx")
        assert o._code_model_active is False  # → prompt agentique normal

    def test_no_op_si_pas_de_modele_code(self, monkeypatch):
        _set_models(monkeypatch, code="")
        o = _fake_orch("general-35b")
        Orchestrator._route_model(o, "feature")
        o.llm.switch_to.assert_not_called()

    def test_respecte_un_choix_manuel_de_modele(self, monkeypatch):
        # L'utilisateur a sélectionné un 3e modèle dans l'UI → on n'y touche pas.
        _set_models(monkeypatch)
        o = _fake_orch("un-modele-choisi-a-la-main")
        Orchestrator._route_model(o, "feature")
        o.llm.switch_to.assert_not_called()
        assert o._code_model_active is False

    def test_force_generalist_garde_le_generaliste_sur_tache_code(self, monkeypatch):
        # Skill interactif (QCM) : même une tâche « feature » reste sur le
        # généraliste (prompt complet → skill injecté), pas de coder-slim.
        _set_models(monkeypatch)
        o = _fake_orch("general-35b")
        Orchestrator._route_model(o, "feature", force_generalist=True)
        o.llm.switch_to.assert_called_once_with("general-35b", "http://localhost:8080/v1", "mlx")
        assert o._code_model_active is False


# ── Détection d'un skill interactif (QCM) ─────────────────────────────────────


class TestSkillInteractif:
    def test_drapeau_explicite(self):
        assert _skill_is_interactive({"interactive": True, "content": ""}) is True

    def test_deux_marqueurs_dans_le_contenu_suffisent(self):
        s = {"content": "Étape 1 — Profilage par QCM. Chaque question à choix multiple."}
        assert _skill_is_interactive(s) is True

    def test_un_seul_marqueur_insuffisant(self):
        assert _skill_is_interactive({"content": "réponds à ce questionnaire"}) is False

    def test_howto_statique_non_interactif(self):
        s = {"content": "## Principes directeurs\nFais ceci, vérifie cela, évite l'autre."}
        assert _skill_is_interactive(s) is False


class TestDetectInteractiveSkill:
    def test_top_skill_interactif_detecte(self, monkeypatch):
        # L'always-on est ignoré ; le 1er how-to (interactif) déclenche True.
        fake = [
            {"slug": "utilisateur_profil", "content": "qcm à choix multiple"},
            {"slug": "concevoir_un_algorithme_pas_a_pas", "interactive": True, "content": "x"},
        ]
        monkeypatch.setattr("agent.orchestrator.load_skills", lambda: fake)
        monkeypatch.setattr("agent.orchestrator.select_skills", lambda _s, _q: fake)
        assert Orchestrator._detect_interactive_skill(MagicMock(), "concevoir un algo") is True

    def test_top_skill_statique_non_detecte(self, monkeypatch):
        fake = [{"slug": "mixage_mastering", "content": "## Principes directeurs"}]
        monkeypatch.setattr("agent.orchestrator.load_skills", lambda: fake)
        monkeypatch.setattr("agent.orchestrator.select_skills", lambda _s, _q: fake)
        assert Orchestrator._detect_interactive_skill(MagicMock(), "mixe ce morceau") is False

    def test_robuste_si_selection_echoue(self, monkeypatch):
        def _boom():
            raise RuntimeError("catalogue KO")
        monkeypatch.setattr("agent.orchestrator.load_skills", _boom)
        assert Orchestrator._detect_interactive_skill(MagicMock(), "x") is False


# ── Prompt slim injecté quand le coder est actif ──────────────────────────────


class TestSlimPromptCoder:
    def test_coder_actif_injecte_un_prompt_slim(self):
        o = MagicMock()
        o._code_model_active = True
        o.memory.messages = []
        o._on_skills_selected = None
        Orchestrator._inject_system_prompt(o, task_type="feature", query="une horloge")
        sysmsg = o.memory.messages[0]
        assert sysmsg["role"] == "system"
        assert "générateur de code" in sysmsg["content"]
        assert "```html" in sysmsg["content"]
        # Slim = bien plus court que le prompt agentique géant (~12k tokens).
        assert len(sysmsg["content"]) < 2000
        assert o._injected_skill_slugs == []


# ── Best-of-N coupé quand le coder est routé ──────────────────────────────────


def _bon_orch(*, enabled=True, force=False, coder=False, use_bon=True) -> Orchestrator:
    """Orchestrateur nu (sans __init__) avec juste les attributs lus par
    _should_run_best_of_n."""
    o = Orchestrator.__new__(Orchestrator)
    o._best_of_n_enabled = enabled
    o._best_of_n_force = force
    o._code_model_active = coder
    o.last_routing = SimpleNamespace(use_best_of_n=use_bon)
    return o


class TestBestOfNSkipCoder:
    def test_bon_actif_tache_hard_hors_coder(self):
        o = _bon_orch(use_bon=True)
        assert o._should_run_best_of_n(0) is True
        assert o._should_run_best_of_n(1) is False  # uniquement la 1ère itération

    def test_bon_coupe_quand_coder_actif(self):
        o = _bon_orch(coder=True, use_bon=True)
        assert o._should_run_best_of_n(0) is False  # ← le cœur du fix

    def test_force_ne_ressuscite_pas_bon_sur_coder(self):
        o = _bon_orch(coder=True, force=True, use_bon=True)
        assert o._should_run_best_of_n(0) is False

    def test_bon_off_si_globalement_desactive(self):
        o = _bon_orch(enabled=False, use_bon=True)
        assert o._should_run_best_of_n(0) is False

    def test_bon_off_si_routing_sans_best_of_n(self):
        o = _bon_orch(use_bon=False)
        assert o._should_run_best_of_n(0) is False

    def test_force_relance_bon_hors_coder(self):
        o = _bon_orch(force=True, use_bon=False)
        assert o._should_run_best_of_n(0) is True

    def test_pas_de_routing_pas_de_bon(self):
        o = _bon_orch(use_bon=True)
        o.last_routing = None
        assert o._should_run_best_of_n(0) is False

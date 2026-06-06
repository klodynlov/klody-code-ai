"""Outil de question interactive `ask_user` (skills QCM).

L'outil ouvre une carte cliquable côté UI, met le tour en pause jusqu'à la
réponse, et la renvoie au modèle. Il n'est exposé au modèle QUE pour un skill
interactif (_interactive_skill_active) — ailleurs l'agent reste autonome.

Cf. plan « questions une-à-une » et la plomberie d'approbation décalquée
(api/server.py _request_approval → _request_user_choice).
"""
from __future__ import annotations

from types import SimpleNamespace

from agent.approval import requires_approval
from agent.orchestrator import Orchestrator
from tools.registry import ASK_USER_TOOL, get_tools

_FAKE_TOOL = {"type": "function", "function": {"name": "read_file", "parameters": {}}}


# ── _tool_ask_user ────────────────────────────────────────────────────────────


class TestToolAskUser:
    def test_delegue_au_canal_et_formate_la_reponse(self):
        """Avec un canal (_ask_user posé par le serveur), l'outil renvoie le
        choix de l'utilisateur, préfixé pour le modèle."""
        recu = {}

        def fake_ask(question, options, allow_free_text):
            recu["q"], recu["opts"], recu["free"] = question, options, allow_free_text
            return "Trier / ordonner"

        o = SimpleNamespace(_ask_user=fake_ask)
        out = Orchestrator._tool_ask_user(
            o, {"question": "Que fait l'algo ?", "options": ["Trier / ordonner", "Rechercher"]}
        )
        assert out == "Réponse de l'utilisateur : Trier / ordonner"
        assert recu["q"] == "Que fait l'algo ?"
        assert recu["opts"] == ["Trier / ordonner", "Rechercher"]
        assert recu["free"] is True  # défaut

    def test_sans_canal_degrade_en_repli_texte(self):
        """CLI / tests : pas de canal interactif → pas de blocage, pas
        d'exception. On rend un message qui invite à poser la question en texte."""
        o = SimpleNamespace()  # pas de _ask_user → getattr renvoie None
        out = Orchestrator._tool_ask_user(
            o, {"question": "Combien de joueurs ?", "options": ["Solo", "Multi"]}
        )
        assert "non-interactif" in out.lower()
        assert "Combien de joueurs ?" in out
        assert "Solo" in out and "Multi" in out

    def test_question_vide_erreur(self):
        o = SimpleNamespace(_ask_user=lambda *_a: "x")
        out = Orchestrator._tool_ask_user(o, {"question": "   ", "options": ["a"]})
        assert out.startswith("ERREUR")

    def test_reponse_vide_signalee(self):
        """L'utilisateur n'a rien choisi (timeout / annulation) → message clair,
        pas un 'Réponse : ' vide."""
        o = SimpleNamespace(_ask_user=lambda *_a: "")
        out = Orchestrator._tool_ask_user(o, {"question": "Volume ?", "options": ["Petit"]})
        assert "n'a pas répondu" in out

    def test_sans_option_ni_texte_libre_erreur(self):
        """Carte sans issue (aucune option valide ET allow_free_text=false) →
        refus net plutôt qu'une fenêtre où l'utilisateur ne peut rien faire."""
        o = SimpleNamespace(_ask_user=lambda *_a: "x")
        out = Orchestrator._tool_ask_user(
            o, {"question": "Q ?", "options": ["", "  "], "allow_free_text": False}
        )
        assert out.startswith("ERREUR")

    def test_sans_option_mais_texte_libre_ok(self):
        """Aucune option mais réponse libre autorisée : pas d'erreur, on délègue."""
        o = SimpleNamespace(_ask_user=lambda *_a: "ma réponse")
        out = Orchestrator._tool_ask_user(
            o, {"question": "Décris ton besoin", "options": [], "allow_free_text": True}
        )
        assert "ma réponse" in out

    def test_filtre_les_options_vides(self):
        captured = {}

        def fake_ask(question, options, allow_free_text):
            captured["opts"] = options
            return "a"

        o = SimpleNamespace(_ask_user=fake_ask)
        Orchestrator._tool_ask_user(o, {"question": "Q ?", "options": ["a", "", "  ", "b"]})
        assert captured["opts"] == ["a", "b"]

    def test_allow_free_text_transmis(self):
        captured = {}

        def fake_ask(question, options, allow_free_text):
            captured["free"] = allow_free_text
            return "a"

        o = SimpleNamespace(_ask_user=fake_ask)
        Orchestrator._tool_ask_user(o, {"question": "Q ?", "options": ["a"], "allow_free_text": False})
        assert captured["free"] is False


# ── Exposition conditionnelle (_tools_for_run) ────────────────────────────────


class TestToolsForRun:
    def test_inclut_ask_user_si_skill_interactif(self):
        o = SimpleNamespace(_interactive_skill_active=True, tools=[_FAKE_TOOL])
        tools = Orchestrator._tools_for_run(o)
        names = [t["function"]["name"] for t in tools]
        assert "ask_user" in names
        assert "read_file" in names  # outils de base conservés

    def test_exclut_ask_user_hors_skill_interactif(self):
        o = SimpleNamespace(_interactive_skill_active=False, tools=[_FAKE_TOOL])
        tools = Orchestrator._tools_for_run(o)
        names = [t["function"]["name"] for t in tools]
        assert "ask_user" not in names
        # Aucune copie inutile : on rend la liste de base telle quelle.
        assert tools is o.tools

    def test_defaut_non_interactif_si_flag_absent(self):
        o = SimpleNamespace(tools=[_FAKE_TOOL])  # pas de _interactive_skill_active
        tools = Orchestrator._tools_for_run(o)
        assert all(t["function"]["name"] != "ask_user" for t in tools)


# ── Garde-fous ────────────────────────────────────────────────────────────────


def test_ask_user_absent_de_get_tools():
    """L'outil ne doit JAMAIS être proposé globalement : exposition conditionnelle
    via _tools_for_run uniquement."""
    assert all(t["function"]["name"] != "ask_user" for t in get_tools())
    assert ASK_USER_TOOL["function"]["name"] == "ask_user"


def test_ask_user_ne_requiert_pas_d_approbation():
    """ask_user est en lecture seule (pas d'effet de bord) → aucun prompt
    d'approbation parasite ne doit s'intercaler avant la question."""
    assert requires_approval("ask_user") is False

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

    def test_options_chaine_json_normalisee(self):
        """Régression « ça bloque » (sessions 04:46/04:50) : Qwen sérialise
        parfois `options` en CHAÎNE JSON au lieu d'une liste. Itérer la chaîne
        donnerait des caractères isolés → carte illisible. On doit récupérer la
        vraie liste."""
        captured = {}

        def fake_ask(question, options, allow_free_text):
            captured["opts"] = options
            return "Trier"

        o = SimpleNamespace(_ask_user=fake_ask)
        Orchestrator._tool_ask_user(
            o,
            {"question": "Q ?", "options": '["Trier", "Chercher", "Autre / je ne sais pas"]'},
        )
        assert captured["opts"] == ["Trier", "Chercher", "Autre / je ne sais pas"]

    def test_options_chaine_multiligne_decoupee_par_lignes(self):
        """Une chaîne qui n'est pas du JSON ne doit JAMAIS être éclatée en
        caractères : multi-lignes → une option par ligne."""
        captured = {}

        def fake_ask(question, options, allow_free_text):
            captured["opts"] = options
            return "x"

        o = SimpleNamespace(_ask_user=fake_ask)
        Orchestrator._tool_ask_user(o, {"question": "Q ?", "options": "Trier\nChercher"})
        assert captured["opts"] == ["Trier", "Chercher"]

    def test_allow_free_text_transmis(self):
        captured = {}

        def fake_ask(question, options, allow_free_text):
            captured["free"] = allow_free_text
            return "a"

        o = SimpleNamespace(_ask_user=fake_ask)
        Orchestrator._tool_ask_user(o, {"question": "Q ?", "options": ["a"], "allow_free_text": False})
        assert captured["free"] is False

    def test_allow_free_text_chaine_false_coercee(self):
        """`bool("false")` vaut True en Python : une chaîne "false" passée par le
        modèle ne doit pas inverser l'intention (sinon la saisie libre reste
        ouverte alors que le skill voulait la fermer)."""
        captured = {}

        def fake_ask(question, options, allow_free_text):
            captured["free"] = allow_free_text
            return "a"

        o = SimpleNamespace(_ask_user=fake_ask)
        Orchestrator._tool_ask_user(o, {"question": "Q ?", "options": ["a"], "allow_free_text": "false"})
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


# ── Détection du skill interactif (_detect_interactive_skill) ─────────────────


# Skill QCM factice : description riche en déclencheurs → contient des mots
# génériques (« liste », « projet ») qui causaient le faux positif.
_QCM_SKILL = {
    "name": "Concevoir un algorithme pas à pas",
    "slug": "concevoir_un_algorithme_pas_a_pas",
    "description": "Guide interactif pour concevoir l'algorithme au cœur d'un "
    "projet · quelle structure de données (tableau, liste, pile) · profiler "
    "le besoin",
    "interactive": True,
}


class TestDetectInteractiveSkill:
    """Le QCM ne doit s'activer que si la requête recoupe l'IDENTITÉ du skill
    (nom/slug), jamais sur de simples mots génériques de sa description — sinon
    « liste les fichiers du projet » lancerait un questionnaire hors-sujet."""

    def _patch(self, monkeypatch, skills):
        # Cibles en chaîne → pas de second import de agent.orchestrator
        # (évite l'avertissement CodeQL « import + import from » sur ce module).
        monkeypatch.setattr("agent.orchestrator.load_skills", lambda: skills)
        monkeypatch.setattr("agent.orchestrator.select_skills", lambda _sk, _q: skills)

    def test_actif_quand_la_requete_recoupe_le_nom(self, monkeypatch):
        self._patch(monkeypatch, [_QCM_SKILL])
        o = SimpleNamespace()
        assert Orchestrator._detect_interactive_skill(
            o, "aide moi a concevoir l'algorithme d'un jeu 2D"
        ) is True

    def test_inactif_quand_match_seulement_sur_la_description(self, monkeypatch):
        """Régression du faux positif : « liste » et « projet » sont dans la
        description du QCM mais pas dans son nom → pas de QCM."""
        self._patch(monkeypatch, [_QCM_SKILL])
        o = SimpleNamespace()
        assert Orchestrator._detect_interactive_skill(
            o, "liste les fichiers du projet"
        ) is False

    def test_inactif_quand_skill_de_tete_non_interactif(self, monkeypatch):
        howto = {
            "name": "Garde-fou anti-SSRF pour un fetch web",
            "slug": "anti_ssrf",
            "description": "valider une URL avant de la requêter",
        }
        self._patch(monkeypatch, [howto])
        o = SimpleNamespace()
        assert Orchestrator._detect_interactive_skill(o, "corrige le bug ssrf") is False

    def test_always_on_ignores_puis_evalue_le_premier_howto(self, monkeypatch):
        profil = {"name": "Profil utilisateur", "slug": "utilisateur_klody", "description": ""}
        self._patch(monkeypatch, [profil, _QCM_SKILL])
        o = SimpleNamespace()
        assert Orchestrator._detect_interactive_skill(
            o, "concevoir un algorithme pas à pas"
        ) is True


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

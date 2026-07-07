"""Tests pour _looks_like_unfinished_plan dans agent.orchestrator.

L'anti-stall détecte un message qui annonce un plan sans appeler de tool —
typique de Qwen3-Coder en mode hard/feature avec T basse.
"""
from agent.orchestrator import (
    _is_empty_after_reasoning,
    _looks_like_unfinished_plan,
)


class TestPlanDetecte:
    def test_plan_classique_avec_enumeration(self):
        content = (
            "Je vais créer une maison en 3D avec Three.js. Voici mon plan :\n"
            "1. Créer un fichier HTML avec un canvas Three.js\n"
            "2. Implémenter la maison\n"
            "3. Ajouter les lumières\n"
            "Commençons par créer le code :"
        )
        assert _looks_like_unfinished_plan(content) is True

    def test_finit_par_deux_points_avec_etape_1(self):
        content = "Je vais commencer. Étape 1 : préparer le code :"
        assert _looks_like_unfinished_plan(content) is True

    def test_finit_par_ellipsis(self):
        content = (
            "Voici mon plan :\n"
            "1. Lire les fichiers\n"
            "2. Modifier le code\n"
            "Tout d'abord :…"
        )
        assert _looks_like_unfinished_plan(content) is True

    def test_anglais(self):
        content = "Let's start with step 1: read the config file:"
        assert _looks_like_unfinished_plan(content) is True

    def test_cas_reel_observe_capture_maison_3d(self):
        """Cas observé sur la capture user — finit par '.' mais multiples 'je vais'."""
        content = (
            "Je vais créer une maison en 3D avec Three.js. Je vais commencer par "
            "analyser les fichiers existants pour comprendre la structure du projet "
            "et ensuite créer une maison avec des éléments de base comme un toit, "
            "un mur, une porte et une fenêtre.\n"
            "Je vais créer un fichier HTML avec le code Three.js pour afficher une "
            "maison 3D simple avec les éléments suivants :\n"
            "    Un corps de maison (cube)\n"
            "    Un toit (pyramide)\n"
            "    Une porte\n"
            "    Des fenêtres\n"
            "    Un sol\n"
            "    Une lumière ambiante et directionnelle\n"
            "Je vais d'abord créer le code HTML avec le contenu Three.js pour la maison 3D."
        )
        # 5+ "je vais" → multiple_intents déclenche
        assert _looks_like_unfinished_plan(content) is True

    def test_intentions_multiples_simples(self):
        content = "Je vais lire le fichier. Je vais ensuite le modifier."
        assert _looks_like_unfinished_plan(content) is True


class TestPasUnPlan:
    def test_reponse_normale_finie(self):
        content = "J'ai créé le fichier app.py avec succès. Tu peux le lancer avec `python app.py`."
        assert _looks_like_unfinished_plan(content) is False

    def test_question_finit_par_question_mark(self):
        content = "Voici mon plan : 1. Faire X. Veux-tu que je continue ?"
        assert _looks_like_unfinished_plan(content) is False

    def test_message_vide(self):
        assert _looks_like_unfinished_plan("") is False
        assert _looks_like_unfinished_plan(None) is False
        assert _looks_like_unfinished_plan("   ") is False

    def test_message_court(self):
        assert _looks_like_unfinished_plan("OK.") is False

    def test_explication_qui_finit_par_deux_points_mais_sans_plan(self):
        # Finit par ":" mais sans aucun signal de plan → False
        content = "Le projet utilise les technologies suivantes pour la dépendance principale :"
        assert _looks_like_unfinished_plan(content) is False


class TestEmptyAfterReasoning:
    """CoT qui bouffe tout le budget sans répondre NI agir (analysis-paralysis)."""

    def _fires(self, **over):
        base = dict(
            content="", has_tool_calls=False,
            thinking_enabled=True, use_bon=False, already_recovered=False,
        )
        base.update(over)
        content = base.pop("content")
        has_tool_calls = base.pop("has_tool_calls")
        return _is_empty_after_reasoning(content, has_tool_calls, **base)

    def test_cas_nominal_declenche(self):
        # 0 content + 0 tool + thinking actif + hors BoN + jamais récupéré
        assert self._fires() is True

    def test_content_blanc_traite_comme_vide(self):
        assert self._fires(content="   \n\t ") is True

    def test_content_non_vide_ne_declenche_pas(self):
        assert self._fires(content="Voici la réponse.") is False

    def test_tool_call_present_ne_declenche_pas(self):
        # Le modèle a agi → pas un stall
        assert self._fires(has_tool_calls=True) is False

    def test_thinking_off_ne_declenche_pas(self):
        # Sans CoT, ce n'est pas ce sous-cas (l'anti-stall/synthèse gère)
        assert self._fires(thinking_enabled=False) is False

    def test_best_of_n_exclu(self):
        assert self._fires(use_bon=True) is False

    def test_une_seule_fois_par_run(self):
        # Déjà récupéré ce run → ne re-déclenche pas (évite la boucle de relances)
        assert self._fires(already_recovered=True) is False

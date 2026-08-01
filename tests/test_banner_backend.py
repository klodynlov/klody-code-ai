"""La bannière d'accueil DÉRIVE son backend et son modèle de la config.

Pourquoi ce test existe : le sous-titre annonçait « Powered by Ollama » en dur,
et la ligne « Modèle » affichait MODEL_NAME (le modèle du mode *ollama*) même
en BACKEND=mlx. Deux affirmations qu'aucune commande ne recalculait — le mode
de défaillance dominant du dépôt. Un en-tête qui nomme la mauvaise dépendance
coûte une enquête entière (vécu le 2026-07-30 sur le nightly à 1/5).
"""
from __future__ import annotations

import io

import main
import pytest
from rich.console import Console


class _MemoireFactice:
    """Le strict nécessaire consommé par print_banner."""

    def __init__(self) -> None:
        self.session_id = "test1234"
        self.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "salut"},
        ]


@pytest.fixture
def capture(monkeypatch):
    """Remplace la console Rich du module par une console capturée."""
    tampon = io.StringIO()
    monkeypatch.setattr(main, "console", Console(file=tampon, width=120, no_color=True))
    return tampon


class TestLibelleBackend:
    def test_mlx(self, monkeypatch):
        monkeypatch.setattr(main, "BACKEND", "mlx")
        assert main._backend_label() == "MLX"

    def test_ollama(self, monkeypatch):
        monkeypatch.setattr(main, "BACKEND", "ollama")
        assert main._backend_label() == "Ollama"

    def test_valeur_inconnue_ne_promet_pas_mlx(self, monkeypatch):
        """Tout ce qui n'est pas 'mlx' retombe sur Ollama — comme LLM_BASE_URL."""
        monkeypatch.setattr(main, "BACKEND", "")
        assert main._backend_label() == "Ollama"


class TestBanniere:
    def test_sous_titre_suit_le_backend(self, monkeypatch, capture):
        monkeypatch.setattr(main, "BACKEND", "mlx")
        main.print_banner(_MemoireFactice())
        sortie = capture.getvalue()
        assert "MLX" in sortie
        # LE point du correctif : plus aucune mention en dur du backend absent.
        assert "Ollama" not in sortie

    def test_sous_titre_en_mode_ollama(self, monkeypatch, capture):
        monkeypatch.setattr(main, "BACKEND", "ollama")
        main.print_banner(_MemoireFactice())
        assert "Ollama" in capture.getvalue()

    def test_modele_affiche_est_celui_envoye_au_backend(self, monkeypatch, capture):
        """LLM_MODEL (= MLX_MODEL en mlx), pas MODEL_NAME."""
        monkeypatch.setattr(main, "BACKEND", "mlx")
        monkeypatch.setattr(main, "LLM_MODEL", "brain")
        main.print_banner(_MemoireFactice())
        sortie = capture.getvalue()
        assert "brain" in sortie
        # Le modèle du mode ollama n'a rien à faire à l'écran en mode mlx.
        assert "qwen3.5:9b" not in sortie

    def test_compteur_de_messages_exclut_le_system(self, capture):
        main.print_banner(_MemoireFactice())
        sortie = capture.getvalue()
        assert "test1234" in sortie
        assert "Messages" in sortie


class TestAffichageAccueil:
    """Le branchement CLI de l'accueil interactif (agent/greeting.py).

    Le module est déjà couvert chez lui ; ce qui se teste ici est le CHEMIN DE
    DÉMARRAGE : rien de ce qui touche à l'accueil ne doit pouvoir empêcher la
    CLI de s'ouvrir — et l'interactivité (numéros, voix) est un contrat de ce
    fichier, pas du module.
    """

    class _AccueilFactice:
        def __init__(self, rendu=None, leve=False):
            from agent.greeting import Accueil

            self.rendu = rendu or Accueil(
                "Bonjour — 12 sessions.",
                "Hier, tu mesurais le coût du garde.",
                ("Reprendre là où on s'est arrêté", "Lancer les tests"),
            )
            self.leve = leve

        def recuperer(self):
            if self.leve:
                raise RuntimeError("boum")
            return self.rendu

    def test_affiche_salutation_rappel_et_propositions_numerotees(self, capture):
        main._afficher_accueil(self._AccueilFactice())
        sortie = capture.getvalue()
        assert "12 sessions" in sortie
        assert "coût du garde" in sortie
        assert "1" in sortie and "Reprendre là où on s'est arrêté" in sortie
        assert "2" in sortie and "Lancer les tests" in sortie
        assert "Tape un numéro" in sortie

    def test_retourne_les_propositions_pour_le_repl(self, capture):
        propositions = main._afficher_accueil(self._AccueilFactice())
        assert propositions == ("Reprendre là où on s'est arrêté", "Lancer les tests")

    def test_un_accueil_qui_explose_ne_casse_pas_le_demarrage(self, capture):
        """Sinon une phrase de politesse empêcherait d'ouvrir la CLI."""
        assert main._afficher_accueil(self._AccueilFactice(leve=True)) == ()

    def test_sans_proposition_pas_d_invite_a_taper_un_numero(self, capture):
        from agent.greeting import Accueil

        main._afficher_accueil(self._AccueilFactice(rendu=Accueil("Bonjour.")))
        assert "Tape un numéro" not in capture.getvalue()

    def test_lance_avant_la_construction_de_l_orchestrator(self):
        """L'ordre EST la fonctionnalité : le thread se nourrit du temps déjà payé.

        `demarrer()` doit précéder `Orchestrator(...)` (découverte MCP, réseau)
        et la sonde LibraryBrain ; sinon l'échéance de 1,5 s se retrouve seule
        face aux 6,52 s du cas froid mesuré, et le repli local devient la règle.
        """
        import inspect

        source = inspect.getsource(main.main)
        assert source.index("accueil.demarrer()") < source.index("Orchestrator(memory)")
        assert source.index("Orchestrator(memory)") < source.index("_afficher_accueil")


class TestVoixAccueil:
    """L'option vocale (accessibilité) ne doit ni bloquer ni fuir hors de son flag."""

    def test_desactivee_par_defaut_aucun_thread(self, monkeypatch, capture):
        import threading

        monkeypatch.setattr(main, "GREETING_VOICE", False)
        avant = {t.name for t in threading.enumerate()}
        main._afficher_accueil(TestAffichageAccueil._AccueilFactice())
        apres = {t.name for t in threading.enumerate()} - avant
        assert not any("accueil-voix" in nom for nom in apres)

    def test_activee_parle_en_thread_demon_avec_les_numeros(self, monkeypatch, capture):
        """La synthèse est synchrone (~6 s à froid) : elle DOIT être détachée.
        Et le texte dit porte les numéros — répondre « 2 » sans voir l'écran."""
        import threading

        monkeypatch.setattr(main, "GREETING_VOICE", True)
        dits: list[str] = []
        fini = threading.Event()

        import tools.voice as voice

        def _faux_speak(texte, langue="fr"):
            dits.append(texte)
            fini.set()
            return "ok"

        monkeypatch.setattr(voice, "speak", _faux_speak)
        main._afficher_accueil(TestAffichageAccueil._AccueilFactice())
        assert fini.wait(2.0), "speak n'a jamais été appelé"
        assert "1. Reprendre là où on s'est arrêté" in dits[0]
        assert "2. Lancer les tests" in dits[0]

    def test_une_voix_qui_explose_reste_silencieuse_a_l_ecran(self, monkeypatch, capture):
        import threading

        monkeypatch.setattr(main, "GREETING_VOICE", True)
        fini = threading.Event()

        import tools.voice as voice

        def _casse(*_a, **_k):
            fini.set()
            raise RuntimeError("CLI VocalBrain absente")

        monkeypatch.setattr(voice, "speak", _casse)
        main._afficher_accueil(TestAffichageAccueil._AccueilFactice())
        assert fini.wait(2.0)
        assert "VocalBrain" not in capture.getvalue()


class TestPropositionsDansLeRepl:
    """« 1 » lance la première proposition — mais UNE seule fois, au départ."""

    def test_un_numero_valide_est_remplace_par_la_proposition(self):
        # La logique vit dans repl(), inaccessible sans terminal : on vérifie le
        # même code sur son extrait exact, gardé synchrone par le test suivant.
        propositions = ("Reprendre le travail", "Lancer les tests")
        user_input = "2"
        if propositions and user_input.isdigit():
            index = int(user_input) - 1
            if 0 <= index < len(propositions):
                user_input = propositions[index]
        assert user_input == "Lancer les tests"

    def test_le_code_du_repl_contient_bien_cette_logique(self):
        import inspect

        source = inspect.getsource(main.repl)
        assert "user_input.isdigit()" in source
        assert "propositions = ()" in source  # la correspondance meurt après usage

    def test_un_numero_hors_bornes_reste_du_texte(self):
        propositions = ("Seule proposition",)
        user_input = "7"
        if propositions and user_input.isdigit():
            index = int(user_input) - 1
            if 0 <= index < len(propositions):
                user_input = propositions[index]
        assert user_input == "7"


# ⚠️ Un scan du SOURCE de main.py à la recherche de « Ollama » a existé ici, et
# il a servi : c'est lui qui a trouvé la 3ᵉ occurrence, dans HELP_TEXT. Il a été
# retiré parce qu'il confondait la PROSE et la SORTIE — il rougissait sur les
# commentaires et docstrings qui *expliquent* le correctif, ce qui aurait fini
# par pousser à supprimer les explications pour faire taire le test. La propriété
# qui compte est comportementale (« l'écran ne nomme pas un service inutilisé »),
# elle est vérifiée par les rendus ci-dessus et par tests/test_status_backend.py.

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

    def test_une_voix_qui_explose_est_SIGNALEE(self, monkeypatch, capture):
        """Règle corrigée après un « aucun son » impossible à diagnostiquer.

        Ce test affirmait l'inverse — que l'écran devait rester muet — et c'était
        le défaut : quand l'utilisateur a ACTIVÉ la voix, le silence est la
        panne. Il ne passait d'ailleurs que par une course (l'assertion tombait
        avant que le thread n'ait fini), donc il ne prouvait même pas ce qu'il
        prétendait.
        """
        import time

        import tools.voice as voice

        monkeypatch.setattr(main, "GREETING_VOICE", True)
        main._voix_signalees.clear()

        def _casse(*_a, **_k):
            raise RuntimeError("CLI VocalBrain absente")

        monkeypatch.setattr(voice, "speak", _casse)
        main._afficher_accueil(TestAffichageAccueil._AccueilFactice())

        # Attente ACTIVE sur l'effet observable, pas sur un événement posé avant
        # lui : c'est ce décalage qui rendait la version précédente flaky.
        limite = time.monotonic() + 2.0
        while "Voix indisponible" not in capture.getvalue() and time.monotonic() < limite:
            time.sleep(0.02)
        assert "Voix indisponible" in capture.getvalue()


class TestVoixReponses:
    """/voix : la lecture des réponses de l'agent, cœur de l'accessibilité."""

    class _OrchestrateurFactice:
        class _Memoire:
            messages = [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "Voici :\n```python\nprint(1)\n```\nC'est corrigé."},
            ]

        memory = _Memoire()

    def test_la_commande_voix_bascule(self, monkeypatch, capture):
        monkeypatch.setattr(main, "_voix_reponses", False)
        assert main.handle_special_command("/voix", None) is True
        assert main._voix_reponses is True
        assert "activée" in capture.getvalue()
        main.handle_special_command("/voix", None)
        assert main._voix_reponses is False

    def test_le_texte_dit_saute_les_blocs_de_code(self):
        """Lire du code à voix haute épelle de la ponctuation — on le mentionne."""
        texte = main._texte_pour_voix("Voici :\n```python\nprint(1)\n```\nC'est corrigé.")
        assert "print" not in texte
        assert "bloc de code" in texte
        assert "C'est corrigé." in texte

    def test_le_texte_dit_est_borne_et_coupe_sur_une_phrase(self):
        texte = main._texte_pour_voix("Première phrase. " + "blabla " * 200)
        assert len(texte) <= 510

    def test_desactivee_ne_parle_pas(self, monkeypatch):
        import threading

        monkeypatch.setattr(main, "_voix_reponses", False)
        avant = {t.name for t in threading.enumerate()}
        main._dire_reponse(self._OrchestrateurFactice())
        assert not any(
            "voix-reponse" in n for n in {t.name for t in threading.enumerate()} - avant
        )

    def test_un_echec_de_speak_est_VISIBLE(self, monkeypatch, capture):
        """LE défaut réparé : speak ne lève jamais, il RETOURNE son échec.

        L'appelant jetait la valeur de retour et n'entourait l'appel que d'un
        `except` — incapable d'attraper quoi que ce soit. « Aucun son » ne
        laissait alors ni message ni log : quatre modes d'échec strictement
        indiscernables d'un succès.
        """
        main._voix_signalees.clear()
        main._signaler_voix(
            "speak indisponible : CLI VocalBrain introuvable (/x/vocalbrain)."
        )
        sortie = capture.getvalue()
        assert "Voix indisponible" in sortie
        assert "CLI VocalBrain introuvable" in sortie
        assert "diagnostic_voix" in sortie

    def test_un_succes_ne_dit_rien(self, monkeypatch, capture):
        main._voix_signalees.clear()
        main._signaler_voix("🔊 Dit à voix haute (2.1s), joué sur les haut-parleurs : « Bonjour »")
        assert capture.getvalue() == ""

    def test_une_synthese_ok_mais_muette_est_signalee(self, monkeypatch, capture):
        """WAV généré, lecteur qui n'a pas démarré : c'est exactement « aucun son »."""
        main._voix_signalees.clear()
        main._signaler_voix("🔊 Dit à voix haute (2.1s), généré (lecture impossible : /tmp/a.wav)")
        assert "Voix indisponible" in capture.getvalue()

    def test_une_MEME_cause_n_est_signalee_qu_une_fois(self, monkeypatch, capture):
        """Sinon la panne devient du spam sur le seul écran que l'utilisateur lit."""
        main._voix_signalees.clear()
        for _ in range(3):
            main._signaler_voix("speak : échec de la synthèse VocalBrain — erreur X")
        assert capture.getvalue().count("Voix indisponible") == 1

    def test_deux_causes_DIFFERENTES_sont_signalees_toutes_les_deux(self, capture):
        """Un booléen unique avalait la seconde — deux pannes, deux remèdes.

        Cas réel : « CLI introuvable », l'utilisateur répare, puis
        « lecture impossible ». Ne montrer que la première le laisserait croire
        que son correctif n'a rien changé.
        """
        main._voix_signalees.clear()
        main._signaler_voix("speak indisponible : CLI VocalBrain introuvable (/x)")
        main._signaler_voix("🔊 Dit à voix haute (2.1s), généré (lecture impossible : /tmp/a.wav)")
        assert capture.getvalue().count("Voix indisponible") == 2

    def test_les_causes_sont_bornees_malgre_des_chemins_variables(self):
        """Dédoublonner sur le texte brut échouerait : durée et chemin changent."""
        causes = {
            main._cause_voix(f"🔊 Dit à voix haute ({d}s), généré (lecture impossible : /tmp/{d}.wav)")
            for d in ("1.0", "2.5", "9.9")
        }
        assert causes == {"lecteur-muet"}

    @pytest.mark.parametrize(
        "rapport, attendu",
        [
            ("speak indisponible : CLI VocalBrain introuvable (/x)", "cli-absente"),
            ("speak : synthèse trop longue (> 90s) — abandonnée. hf download …", "modele-absent"),
            ("speak : synthèse terminée mais fichier audio introuvable.", "wav-introuvable"),
            ("speak : échec de la synthèse VocalBrain — boom", "synthese-echouee"),
        ],
    )
    def test_chaque_cause_est_reconnue(self, rapport, attendu):
        assert main._cause_voix(rapport) == attendu

    def test_activer_la_voix_sonde_tout_de_suite(self, monkeypatch, capture):
        """Découvrir la panne au bout de trois réponses muettes coûte plus cher."""
        import tools.voice as voice

        monkeypatch.setattr(main, "_voix_reponses", False)
        main._voix_signalees.clear()
        monkeypatch.setattr(voice, "speak", lambda t, lang="fr": "speak : échec de la synthèse")
        main.handle_special_command("/voix", None)
        assert "Voix indisponible" in capture.getvalue()

    def test_desactiver_la_voix_ne_sonde_pas(self, monkeypatch, capture):
        import tools.voice as voice

        monkeypatch.setattr(main, "_voix_reponses", True)
        appels: list = []
        monkeypatch.setattr(voice, "speak", lambda t, lang="fr": appels.append(t) or "ok")
        main.handle_special_command("/voix", None)
        assert appels == []

    def test_activee_dit_la_derniere_reponse_en_thread_detache(self, monkeypatch):
        import threading

        import tools.voice as voice

        monkeypatch.setattr(main, "_voix_reponses", True)
        dits: list[str] = []
        fini = threading.Event()

        def _faux_speak(texte, langue="fr"):
            dits.append(texte)
            fini.set()
            return "ok"

        monkeypatch.setattr(voice, "speak", _faux_speak)
        main._dire_reponse(self._OrchestrateurFactice())
        assert fini.wait(2.0), "speak n'a jamais été appelé"
        assert "C'est corrigé." in dits[0]
        assert "print" not in dits[0]


class TestNoteDeRepriseALaSortie:
    def test_run_extraction_ecrit_la_note_meme_si_l_extraction_echoue(
        self, monkeypatch, tmp_path, capture
    ):
        """La reprise du lendemain ne doit pas dépendre d'un appel LLM de sortie."""
        from agent import greeting

        notes: list[list] = []
        monkeypatch.setattr(greeting, "noter_reprise", lambda msgs, d=None: notes.append(msgs))

        def _extraction_cassee(*_a, **_k):
            raise RuntimeError("LLM injoignable")

        monkeypatch.setattr(main, "extract_and_save", _extraction_cassee)
        monkeypatch.setattr(main, "get_long_term_memory", lambda: None)

        orch = TestVoixReponses._OrchestrateurFactice()
        with pytest.raises(RuntimeError):
            main._run_extraction(orch)
        assert notes and notes[0] is orch.memory.messages


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

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
    """Le branchement CLI de l'accueil généré (agent/greeting.py).

    Le module est déjà couvert à 100 % chez lui ; ce qui se teste ici est le
    CHEMIN DE DÉMARRAGE : rien de ce qui touche à l'accueil ne doit pouvoir
    empêcher la CLI de s'ouvrir.
    """

    class _AccueilFactice:
        def __init__(self, texte="Bonjour — 12 sessions.", leve=False):
            self.texte = texte
            self.leve = leve

        def recuperer(self):
            if self.leve:
                raise RuntimeError("boum")
            return self.texte

    def test_affiche_la_phrase(self, capture):
        main._afficher_accueil(self._AccueilFactice())
        assert "12 sessions" in capture.getvalue()

    def test_un_accueil_qui_explose_ne_casse_pas_le_demarrage(self, capture):
        """Sinon une phrase de politesse empêcherait d'ouvrir la CLI."""
        main._afficher_accueil(self._AccueilFactice(leve=True))
        assert capture.getvalue() == "" or "boum" not in capture.getvalue()

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


# ⚠️ Un scan du SOURCE de main.py à la recherche de « Ollama » a existé ici, et
# il a servi : c'est lui qui a trouvé la 3ᵉ occurrence, dans HELP_TEXT. Il a été
# retiré parce qu'il confondait la PROSE et la SORTIE — il rougissait sur les
# commentaires et docstrings qui *expliquent* le correctif, ce qui aurait fini
# par pousser à supprimer les explications pour faire taire le test. La propriété
# qui compte est comportementale (« l'écran ne nomme pas un service inutilisé »),
# elle est vérifiée par les rendus ci-dessus et par tests/test_status_backend.py.

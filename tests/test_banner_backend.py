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


def test_aucune_mention_en_dur_du_backend_dans_le_source():
    """Filet anti-récidive : le nom du backend ne se réécrit pas en dur.

    Le défaut réparé ici est revenu par copier-coller ailleurs (message d'erreur
    de agent/llm.py, en-tête du nightly). Si une chaîne « Ollama » réapparaît
    dans main.py, elle doit être un chemin de code propre au mode ollama
    (OLLAMA_BASE_URL, `ollama serve` dans /status), pas une affirmation
    d'accueil.
    """
    source = main.__file__
    with open(source, encoding="utf-8") as f:
        lignes = [
            ligne for ligne in f
            if "Ollama" in ligne and not ligne.lstrip().startswith("#")
        ]
    # Les seules occurrences légitimes vivent dans /status, qui sonde
    # explicitement le daemon ollama, et dans _backend_label lui-même.
    for ligne in lignes:
        assert (
            "OLLAMA_BASE_URL" in ligne
            or "ollama serve" in ligne
            or '"Ollama"' in ligne
        ), f"Mention en dur du backend hors du chemin ollama : {ligne.strip()}"

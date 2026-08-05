"""La suite de tests ne parle pas, et n'écrit pas dans l'atelier de l'utilisateur.

Ce fichier existe parce que le garde de `conftest._voix_muette` doit pouvoir
ROUGIR. Sans lui, une régression le retirerait sans qu'aucun test ne tombe : la
suite se remettrait à synthétiser, et le signe serait du son dans la pièce et des
fichiers dans `~/.vocalbrain` — c'est-à-dire rien que pytest regarde.

Ce que le défaut avait de vicieux, et pourquoi on teste l'EFFET et pas le réglage :
il n'a jamais fallu modifier un test pour l'ouvrir. `main.GREETING_VOICE` est lu
à l'import du module, donc poser `GREETING_VOICE=true` dans le `.env` a suffi à
rendre la suite parlante. Un test qui affirmerait « le flag vaut False » serait
donc vert sur la machine du développeur muet et faux partout ailleurs. On mesure
le seul invariant qui tienne : le vrai dossier audio ne bouge pas.
"""
from __future__ import annotations

import io
import time

import config
import main
import pytest
from rich.console import Console


@pytest.fixture
def capture(monkeypatch):
    tampon = io.StringIO()
    monkeypatch.setattr(main, "console", Console(file=tampon, width=120, no_color=True))
    return tampon


def _prises_reelles(dossier) -> set[str]:
    """Inventaire du VRAI dossier audio — vide si l'atelier n'existe pas ici.

    Absent en CI (aucun venv local-suno) : le test y vérifie alors « rien n'a
    été créé » à partir d'un ensemble vide, ce qui reste exactement l'invariant
    voulu — un WAV apparu le ferait tomber.
    """
    if not dossier.exists():
        return set()
    return {str(p) for p in dossier.rglob("*.wav")}


class _AccueilFactice:
    rendu = None

    def __init__(self):
        from agent.greeting import Accueil

        self.rendu = Accueil("Bonjour.", "", ("Reprendre",))

    def recuperer(self):
        return self.rendu


def test_la_CLI_configuree_pendant_la_suite_n_existe_pas():
    """Le détournement doit pointer dans le vide, pas vers un binaire jouable."""
    from pathlib import Path

    assert not Path(config.VOICE_CLI).exists()


def test_l_accueil_vocal_n_ecrit_RIEN_dans_le_vrai_dossier(
    capture, monkeypatch, vrai_dossier_audio
):
    """Le chemin voix est réellement exercé — et ne dépose aucune prise.

    Vécu le 2026-08-02 : ces mêmes appels rendaient 4 WAV par passe, avec le son
    joué sur les haut-parleurs. On vérifie les DEUX moitiés, sinon un chemin
    voix simplement désactivé passerait pour un chemin voix rendu inoffensif.
    """
    avant = _prises_reelles(vrai_dossier_audio)
    monkeypatch.setattr(main, "GREETING_VOICE", True)
    main._voix_signalees.clear()

    main._afficher_accueil(_AccueilFactice())

    # Attente ACTIVE sur l'effet observable : la synthèse vit dans un thread
    # démon, donc pytest peut rendre la main AVANT elle — c'est exactement ce qui
    # rendait la fuite invisible (« 36 passed in 6.04s », les WAV arrivaient après).
    limite = time.monotonic() + 5.0
    while "Voix indisponible" not in capture.getvalue() and time.monotonic() < limite:
        time.sleep(0.02)

    assert "Voix indisponible" in capture.getvalue(), "le chemin voix n'a pas été exercé"
    assert _prises_reelles(vrai_dossier_audio) == avant


def test_la_sonde_de_la_commande_voix_n_ecrit_RIEN_non_plus(
    capture, monkeypatch, vrai_dossier_audio
):
    """La seconde fuite : elle ne consultait pas GREETING_VOICE, donc y survivait.

    `/voix` sonde la voix à l'activation — un vrai `speak`, délibéré côté prod
    (découvrir une voix cassée au bout de trois réponses muettes coûte plus cher
    qu'attendre ici). Ce test ne conteste pas ce choix ; il exige qu'en test la
    sonde ne touche pas l'atelier.
    """
    avant = _prises_reelles(vrai_dossier_audio)
    monkeypatch.setattr(main, "_voix_reponses", False)
    main._voix_signalees.clear()

    main.handle_special_command("/voix", None)

    assert main._voix_reponses is True, "la sonde n'a pas été atteinte"
    assert _prises_reelles(vrai_dossier_audio) == avant

"""Fixtures globales de la suite Klody."""
import logging

import config
import pytest
from agent import semantic_memory

# Capturé AVANT toute redirection : le garde-fou de tests/test_hermeticite_voix.py
# doit pouvoir vérifier que le vrai dossier reste intact, et il ne le peut plus si
# la seule référence qui subsiste est celle qu'on vient de détourner.
_VRAI_VOICE_AUDIO_DIR = config.VOICE_AUDIO_DIR


@pytest.fixture
def vrai_dossier_audio():
    """Le dossier audio RÉEL de l'utilisateur, tel qu'avant `_voix_muette`."""
    return _VRAI_VOICE_AUDIO_DIR


@pytest.fixture(scope="session", autouse=True)
def _pas_de_pollution_du_log_prod():
    """La suite ne DOIT PAS écrire dans le vrai logs/agent.log.

    config.py attache un FileHandler(LOG_FILE) au logger racine dès l'import
    (basicConfig). Sans ce garde-fou, tout test qui exerce VOLONTAIREMENT un
    chemin d'erreur — typiquement tools.voice::TestPannes (« vocalbrain rc=1 :
    Personnage introuvable », « WAV introuvable », « afplay absent ») — écrit des
    lignes WARNING/ERROR dans le journal de PROD. En relecture, ces lignes de test
    passent pour de vraies pannes live (fausse alarme vécue 11/07 : le triplet
    d'erreurs voice était en fait la suite de tests, pas une session réelle).
    On détache les FileHandler le temps de la session, puis on les remet.
    """
    root = logging.getLogger()
    detached = [h for h in list(root.handlers) if isinstance(h, logging.FileHandler)]
    for h in detached:
        root.removeHandler(h)
    yield
    for h in detached:
        root.addHandler(h)


@pytest.fixture(autouse=True)
def _semantic_memory_isolee(monkeypatch, tmp_path):
    """La mémoire sémantique ne touche JAMAIS la base réelle pendant les tests.

    Sans ce garde-fou, tout test qui passe par LongTermMemory.remember() (miroir)
    écrirait dans logs/semantic_memory.db ET chargerait le vrai modèle
    d'embeddings (vécu : 42 entrées de test dans la base de prod). Miroir
    désactivé par défaut ; les tests dédiés (test_semantic_memory) le réactivent
    et configurent explicitement une base tmp_path. La db par défaut est de
    toute façon détournée vers tmp_path (ceinture + bretelles), et l'état
    process du module est remis à zéro entre tests.
    """
    monkeypatch.setattr(config, "SEMANTIC_MEMORY_ENABLED", False)
    monkeypatch.setattr(config, "SEMANTIC_MEMORY_DB", tmp_path / "semantic_memory.db")
    yield
    semantic_memory._provider = None
    semantic_memory._configured_db = None


@pytest.fixture(autouse=True)
def _voix_muette(monkeypatch, tmp_path):
    """La suite ne DOIT JAMAIS faire parler la VRAIE CLI VocalBrain.

    Troisième instance du même défaut que les deux fixtures ci-dessus (le log de
    prod, la base sémantique) : un test touchait la ressource réelle de l'atelier.
    Ici, `tests/test_banner_backend.py` exerce les chemins voix de `main` sans
    rien détourner — donc `speak` lançait la CLI, synthétisait pour de bon, JOUAIT
    LE SON sur les haut-parleurs, et déposait le WAV dans `~/.vocalbrain/audio`
    avec sa ligne dans `vocalbrain.db`. Mesuré le 2026-08-05 : **4 prises par
    passe** de ce seul fichier, 61 prises de fixture accumulées depuis le 02-08.

    Deux fuites distinctes, et c'est ce qui rend le garde nécessaire ICI plutôt
    que test par test :

    1. `main.GREETING_VOICE` est lu à l'IMPORT du module, donc les tests
       héritaient du `.env` du développeur. Ils étaient muets tant qu'il valait
       `false` — poser `GREETING_VOICE=true` a rendu la suite parlante sans
       toucher une ligne de test. Un test dont le comportement dépend du `.env`
       local n'est pas un test.
    2. `handle_special_command("/voix")` sonde la voix à l'activation (un vrai
       `speak`, délibéré côté prod) et ne consulte PAS `GREETING_VOICE` : cette
       fuite-là survivait au flag coupé. Contrôle mesuré : 4 prises → 1.

    On coupe donc à la racine plutôt qu'au cas par cas : `VOICE_CLI` pointe dans
    le vide, d'où un `FileNotFoundError` que `speak` traduit déjà en « CLI
    VocalBrain introuvable » — sans subprocess, sans son, sans fichier. Le
    dossier audio est détourné en prime (ceinture + bretelles). `test_voice.py`,
    lui, reste maître chez lui : sa propre fixture repose ces deux valeurs.
    """
    monkeypatch.setattr(config, "VOICE_CLI", str(tmp_path / "vocalbrain-absent"))
    monkeypatch.setattr(config, "VOICE_AUDIO_DIR", tmp_path / "audio")

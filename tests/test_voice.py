"""Tests de tools/voice.py — outil speak (TTS VocalBrain via CLI + afplay).

Hermétiques : aucun subprocess réel. La CLI est simulée par monkeypatch de
subprocess.run (et écrit un vrai petit WAV là où le glob l'attend), la lecture
par monkeypatch de subprocess.Popen.
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import config
import pytest
from tools import voice

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _write_wav(path: Path, seconds: float = 0.5, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


@pytest.fixture()
def voice_env(monkeypatch, tmp_path):
    """Configure un environnement voix isolé : CLI factice, audio dans tmp."""
    monkeypatch.setattr(config, "VOICE_CLI", "/fake/bin/vocalbrain")
    monkeypatch.setattr(config, "VOICE_PROJECT_ID", "proj-test")
    monkeypatch.setattr(config, "VOICE_CHARACTER", "Klody")
    monkeypatch.setattr(config, "VOICE_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(config, "VOICE_PLAY_CMD", "afplay")
    return tmp_path / "audio"


class _FakeRun:
    """Simule subprocess.run de la CLI : écrit le WAV attendu et capture cmd."""

    def __init__(self, audio_dir: Path, returncode: int = 0, stderr: str = ""):
        self.audio_dir = audio_dir
        self.returncode = returncode
        self.stderr = stderr
        self.last_cmd: list[str] | None = None
        self.last_env: dict[str, str] | None = None

    def __call__(self, cmd, **kwargs):
        self.last_cmd = list(cmd)
        self.last_env = kwargs.get("env")
        if self.returncode == 0:
            seg = cmd[cmd.index("--segment-id") + 1]
            _write_wav(self.audio_dir / "proj-test" / "char-1" / f"{seg}_take01_abc.wav")
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout="", stderr=self.stderr,
        )


# --------------------------------------------------------------------------- #
# Cas nominal                                                                  #
# --------------------------------------------------------------------------- #

class TestSpeak:
    def test_succes_genere_et_joue(self, monkeypatch, voice_env):
        fake_run = _FakeRun(voice_env)
        played: list[list[str]] = []
        monkeypatch.setattr(voice.subprocess, "run", fake_run)
        monkeypatch.setattr(
            voice.subprocess, "Popen",
            lambda cmd, **kw: played.append(list(cmd)) or None,
        )

        result = voice.speak("Bonjour, je suis Klody.")

        assert "Dit à voix haute" in result
        assert "Bonjour, je suis Klody." in result
        # La CLI a reçu projet, personnage, langue par défaut.
        assert fake_run.last_cmd is not None
        assert "-p" in fake_run.last_cmd and "proj-test" in fake_run.last_cmd
        assert "-c" in fake_run.last_cmd and "Klody" in fake_run.last_cmd
        # « fr » mappé vers le nom complet attendu par Qwen3-TTS.
        assert fake_run.last_cmd[fake_run.last_cmd.index("--lang") + 1] == "french"
        # afplay lancé sur le WAV généré.
        assert len(played) == 1
        assert played[0][0] == "afplay"
        assert played[0][1].endswith(".wav")

    def test_langue_mappee_vers_nom_complet(self, monkeypatch, voice_env):
        fake_run = _FakeRun(voice_env)
        monkeypatch.setattr(voice.subprocess, "run", fake_run)
        monkeypatch.setattr(voice.subprocess, "Popen", lambda cmd, **kw: None)

        voice.speak("Hello there.", language="en")
        assert fake_run.last_cmd[fake_run.last_cmd.index("--lang") + 1] == "english"

        # Un nom complet inconnu du map passe tel quel.
        voice.speak("Hello there.", language="french")
        assert fake_run.last_cmd[fake_run.last_cmd.index("--lang") + 1] == "french"

    def test_phrases_separees_par_newline(self, monkeypatch, voice_env):
        # Qwen3-TTS segmente sur '\n' — sans ce découpage, un texte multi-phrases
        # sature max_tokens (WAV de 163 s pour 103 caractères, vécu).
        fake_run = _FakeRun(voice_env)
        monkeypatch.setattr(voice.subprocess, "run", fake_run)
        monkeypatch.setattr(voice.subprocess, "Popen", lambda cmd, **kw: None)

        voice.speak("Première phrase. Deuxième phrase ! Troisième ?")

        sent = fake_run.last_cmd[fake_run.last_cmd.index("-t") + 1]
        assert sent == "Première phrase.\nDeuxième phrase !\nTroisième ?"

    def test_duree_dans_le_compte_rendu(self, monkeypatch, voice_env):
        monkeypatch.setattr(voice.subprocess, "run", _FakeRun(voice_env))
        monkeypatch.setattr(voice.subprocess, "Popen", lambda cmd, **kw: None)

        result = voice.speak("Test durée.")

        assert "(0.5s)" in result


# --------------------------------------------------------------------------- #
# Entrées invalides et troncature                                              #
# --------------------------------------------------------------------------- #

class TestEntrees:
    def test_texte_vide_refuse_sans_subprocess(self, monkeypatch, voice_env):
        def boom(*a, **k):  # pragma: no cover - ne doit jamais être appelé
            raise AssertionError("subprocess.run ne doit pas être appelé")
        monkeypatch.setattr(voice.subprocess, "run", boom)

        assert "texte vide" in voice.speak("   ")

    def test_texte_long_tronque_a_la_phrase(self, monkeypatch, voice_env):
        fake_run = _FakeRun(voice_env)
        monkeypatch.setattr(voice.subprocess, "run", fake_run)
        monkeypatch.setattr(voice.subprocess, "Popen", lambda cmd, **kw: None)

        long_text = ("Première phrase utile. " * 30) + "Fin jamais dite."
        voice.speak(long_text)

        sent = fake_run.last_cmd[fake_run.last_cmd.index("-t") + 1]
        assert len(sent) <= voice._TEXT_CAP
        assert sent.endswith(".")  # coupé sur une fin de phrase, pas au milieu


# --------------------------------------------------------------------------- #
# Pannes — toujours un message, jamais une exception                           #
# --------------------------------------------------------------------------- #

class TestPannes:
    def test_cli_absente(self, monkeypatch, voice_env):
        def raise_fnf(*a, **k):
            raise FileNotFoundError("vocalbrain")
        monkeypatch.setattr(voice.subprocess, "run", raise_fnf)

        result = voice.speak("Bonjour")

        assert "indisponible" in result
        assert "VOICE_CLI" in result

    def test_timeout_synthese(self, monkeypatch, voice_env):
        def raise_timeout(cmd, **k):
            raise subprocess.TimeoutExpired(cmd, 180)
        monkeypatch.setattr(voice.subprocess, "run", raise_timeout)

        assert "trop longue" in voice.speak("Bonjour")

    def test_echec_cli_remonte_stderr(self, monkeypatch, voice_env):
        monkeypatch.setattr(
            voice.subprocess, "run",
            _FakeRun(voice_env, returncode=1, stderr="Personnage introuvable : Klody"),
        )

        result = voice.speak("Bonjour")

        assert "échec" in result
        assert "Personnage introuvable" in result

    def test_wav_introuvable_apres_succes(self, monkeypatch, voice_env):
        # CLI rc=0 mais n'écrit rien (FakeRun avec mauvais dossier).
        monkeypatch.setattr(
            voice.subprocess, "run",
            lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )

        assert "introuvable" in voice.speak("Bonjour")

    def test_lecture_impossible_reste_un_succes(self, monkeypatch, voice_env):
        monkeypatch.setattr(voice.subprocess, "run", _FakeRun(voice_env))

        def popen_fail(*a, **k):
            raise OSError("afplay absent")
        monkeypatch.setattr(voice.subprocess, "Popen", popen_fail)

        result = voice.speak("Bonjour")

        assert "lecture impossible" in result


# --------------------------------------------------------------------------- #
# Mode HORS-LIGNE — jamais de fetch modèle pendant la synthèse                  #
# --------------------------------------------------------------------------- #

class TestModeHorsLigne:
    """La synthèse tourne avec HF_HUB_OFFLINE=1 : le modèle est provisionné à part
    (hf download), jamais téléchargé en cours de route — un fetch HF qui traîne
    faisait stagner speak jusqu'au timeout (vécu 11/07 : poids Qwen3-TTS à moitié
    téléchargés → stall 180 s → aucun son)."""

    def test_subprocess_force_offline(self, monkeypatch, voice_env):
        fake_run = _FakeRun(voice_env)
        monkeypatch.setattr(voice.subprocess, "run", fake_run)
        monkeypatch.setattr(voice.subprocess, "Popen", lambda cmd, **kw: None)

        voice.speak("Bonjour.")

        assert fake_run.last_env is not None
        assert fake_run.last_env.get("HF_HUB_OFFLINE") == "1"
        assert fake_run.last_env.get("TRANSFORMERS_OFFLINE") == "1"

    def test_synth_env_herite_de_l_environnement(self, monkeypatch):
        # L'env de synthèse part de os.environ (PATH & co conservés) + pins offline.
        monkeypatch.setenv("PATH", "/toto/bin")
        env = voice._synth_env()
        assert env["PATH"] == "/toto/bin"
        assert env["HF_HUB_OFFLINE"] == "1"


# --------------------------------------------------------------------------- #
# Indices actionnables — échec « modèle absent » guide vers le fix              #
# --------------------------------------------------------------------------- #

class TestIndicesActionnables:
    def test_timeout_donne_la_commande_de_provisioning(self, monkeypatch, voice_env):
        def raise_timeout(cmd, **k):
            raise subprocess.TimeoutExpired(cmd, 90)
        monkeypatch.setattr(voice.subprocess, "run", raise_timeout)

        result = voice.speak("Bonjour")

        assert "trop longue" in result
        assert "hf download" in result  # indice actionnable

    def test_echec_modele_absent_ajoute_l_indice(self, monkeypatch, voice_env):
        # stderr typique du mode hors-ligne quand les poids manquent.
        stderr = ("LocalEntryNotFoundError: Cannot find the requested files in the "
                  "disk cache and outgoing traffic has been disabled.")
        monkeypatch.setattr(
            voice.subprocess, "run", _FakeRun(voice_env, returncode=1, stderr=stderr),
        )

        result = voice.speak("Bonjour")

        assert "échec" in result
        assert "hf download" in result

    def test_echec_non_lie_au_modele_pas_d_indice(self, monkeypatch, voice_env):
        # Un échec sans rapport (ex. personnage manquant) ne colle PAS l'indice modèle.
        monkeypatch.setattr(
            voice.subprocess, "run",
            _FakeRun(voice_env, returncode=1, stderr="Personnage introuvable : Klody"),
        )

        result = voice.speak("Bonjour")

        assert "échec" in result
        assert "hf download" not in result

    def test_looks_like_model_missing(self):
        assert voice._looks_like_model_missing(
            "outgoing traffic has been disabled") is True
        assert voice._looks_like_model_missing(
            "LocalEntryNotFoundError: ...") is True
        assert voice._looks_like_model_missing("Personnage introuvable") is False
        assert voice._looks_like_model_missing("") is False


# --------------------------------------------------------------------------- #
# Câblage registre / dispatch                                                  #
# --------------------------------------------------------------------------- #

class TestCablage:
    def test_speak_dans_le_registre(self):
        from tools.registry import TOOLS
        names = [t["function"]["name"] for t in TOOLS]
        assert "speak" in names

    def test_speak_dans_le_dispatch(self, monkeypatch, voice_env):
        from agent.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)  # routage seul, sans init
        table = orch._build_dispatch()
        assert "speak" in table

        monkeypatch.setattr(voice.subprocess, "run", _FakeRun(voice_env))
        monkeypatch.setattr(voice.subprocess, "Popen", lambda cmd, **kw: None)
        result = table["speak"]({"text": "Salut."})
        assert "Dit à voix haute" in result

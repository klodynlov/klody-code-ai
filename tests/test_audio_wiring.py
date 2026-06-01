"""Câblage des outils audio : registry → dispatcher → audio.py.

Pas de dépendance librosa requise : les fonctions valident le chemin AVANT
le check librosa, donc on peut tester l'intégration sandbox même sans la lib.
"""

import json

import pytest
from tools.audio import (
    AudioSandboxViolation,
    _validated_path,
    analyze_audio,
    convert_format,
    edit_wav,
    generate_silence,
    get_waveform_data,
    mix_stems,
)
from tools.registry import get_tool_names

AUDIO_TOOL_NAMES = (
    "analyze_audio",
    "edit_wav",
    "mix_stems",
    "generate_silence",
    "convert_format",
    "get_waveform_data",
)


class TestRegistryExposeAudio:
    def test_les_6_outils_audio_sont_enregistres(self):
        names = set(get_tool_names())
        for n in AUDIO_TOOL_NAMES:
            assert n in names, f"manquant dans registry: {n}"

    def test_aucun_doublon_de_nom(self):
        names = get_tool_names()
        assert len(names) == len(set(names))


class TestSandboxEnforcement:
    """Tous les outils audio refusent les chemins hors des racines autorisées."""

    def test_analyze_refuse_chemin_hors_racines(self):
        r = analyze_audio("/etc/hosts")
        assert "hors des racines" in r["error"]

    def test_edit_refuse_chemin_hors_racines(self):
        r = edit_wav("/etc/passwd", output="/tmp/x.wav")
        assert "hors des racines" in r["error"]

    def test_mix_refuse_un_seul_chemin_hors_racines(self, tmp_path):
        (tmp_path / "a.wav").write_bytes(b"")  # juste pour que le chemin existe sous tmp
        # tmp_path n'est pas sous ALLOWED_ROOTS par défaut → refus
        r = mix_stems([str(tmp_path / "a.wav"), "/etc/hosts"])
        assert "hors des racines" in r["error"]

    def test_silence_refuse_output_hors_racines(self):
        r = generate_silence(1.0, output="/etc/x.wav")
        assert "hors des racines" in r["error"]

    def test_convert_refuse_input_hors_racines(self):
        r = convert_format("/etc/hosts")
        assert "hors des racines" in r["error"]

    def test_waveform_refuse_chemin_hors_racines(self):
        r = get_waveform_data("/etc/hosts")
        assert "hors des racines" in r["error"]


class TestValidatedPath:
    def test_chemin_sous_racine_passe(self):
        from config import PROJECT_ROOT
        # config.py existe sous PROJECT_ROOT/klody-code-ai dans la vraie config,
        # mais dans CE repo PROJECT_ROOT=cwd donc config.py existe directement.
        target = PROJECT_ROOT / "config.py"
        if not target.exists():
            pytest.skip("config.py introuvable depuis PROJECT_ROOT")
        assert _validated_path(str(target)) == str(target)

    def test_chemin_hors_racines_leve(self):
        with pytest.raises(AudioSandboxViolation, match="hors des racines"):
            _validated_path("/etc/hosts")

    def test_must_exist_false_accepte_chemin_inexistant_sous_racine(self, tmp_path):
        # Sans must_exist, un fichier qui n'existe pas encore est OK tant que
        # son dossier est sous une racine autorisée.
        from tools import audio as a
        # On élargit temporairement les racines pour inclure tmp_path
        original = a._AUDIO_ROOTS
        try:
            a._AUDIO_ROOTS = original + [tmp_path]
            p = a._validated_path(str(tmp_path / "futur.wav"), must_exist=False)
            assert p == str(tmp_path / "futur.wav")
        finally:
            a._AUDIO_ROOTS = original


class TestDispatcher:
    """L'orchestrator._execute_tool route bien les 6 noms audio."""

    @pytest.fixture
    def orch(self):
        from agent.orchestrator import Orchestrator
        o = Orchestrator.__new__(Orchestrator)

        class _Stub:
            def __getattr__(self, _):
                return lambda *a, **kw: None
        o.profiler = _Stub()
        return o

    @pytest.mark.parametrize("name", AUDIO_TOOL_NAMES)
    def test_dispatch_par_nom(self, orch, name):
        # Tous refusent /etc/hosts via la sandbox. Les outils sans `path` (silence)
        # sont appelés avec leurs args minimaux.
        if name == "generate_silence":
            args = {"duration": 0.1, "output": "/etc/x.wav"}
        elif name == "mix_stems":
            args = {"paths": ["/etc/hosts"]}
        else:
            args = {"path": "/etc/hosts"}
        out = orch._execute_tool(name, args)
        data = json.loads(out)
        assert "hors des racines" in data["error"]

    def test_outil_audio_inconnu_retourne_fallback(self, orch):
        out = orch._execute_tool("audio_inexistant", {})
        assert "Outil inconnu" in out

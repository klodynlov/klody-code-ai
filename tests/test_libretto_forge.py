"""Tests de la chaîne Forge → Libretto (gate) → Gadget → REAPER.

Libretto vit dans un dépôt voisin OPTIONNEL : aucun test ici ne l'exige. Le
sous-processus Forge et la lecture MIDI sont mockés, le pont REAPER aussi —
CI-sûr sur une machine nue.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from klody_mcp import gadget_server as gs, libretto_forge as lf

# --------------------------------------------------------------------------- #
# Découverte de Libretto                                                      #
# --------------------------------------------------------------------------- #


def test_status_absent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIBRETTO_ROOT", str(tmp_path / "nulle-part"))
    st = lf.status()
    assert st["available"] is False and "introuvable" in st["detail"]


@pytest.fixture
def fake_libretto(tmp_path: Path, monkeypatch) -> Path:
    """Faux dépôt Libretto : juste les fichiers que la découverte exige."""
    root = tmp_path / "Libretto"
    (root / "libretto").mkdir(parents=True)
    (root / "examples").mkdir()
    (root / "libretto" / "axes.py").write_text("# stub")
    (root / "examples" / "forge.py").write_text("# stub")
    monkeypatch.setenv("LIBRETTO_ROOT", str(root))
    return root


def test_status_present(fake_libretto: Path):
    st = lf.status()
    assert st["available"] is True and st["forge"] is True


def test_run_forge_sans_libretto(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIBRETTO_ROOT", str(tmp_path / "vide"))
    with pytest.raises(lf.LibrettoUnavailable):
        lf.run_forge(tmp_path / "out")


# --------------------------------------------------------------------------- #
# Forge (sous-processus mocké)                                                #
# --------------------------------------------------------------------------- #


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _fake_report(out_dir: Path, winner: bool = True) -> dict:
    report = {
        "n_requested": 4, "n_generated": 4, "n_rejected_confidence": 0,
        "n_rejected_score": 0, "gates": {"min_confidence": 0.55, "min_score": 0.0},
        "winner": {"form": "aaba", "mode": "maj", "meter": "4/4", "bpm": 112,
                   "score": 0.83, "confidence": 1.0, "level": "élevée"},
        "winner_file": "forge_winner.mid" if winner else None,
    }
    if not winner:
        report["winner"] = None
        report["n_rejected_confidence"] = 4
    (out_dir / "forge_report.json").write_text(json.dumps(report), encoding="utf-8")
    return report


def test_run_forge_ok(fake_libretto: Path, tmp_path: Path, monkeypatch):
    out = tmp_path / "out"
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"], captured["cwd"] = argv, kwargs.get("cwd")
        _fake_report(Path(argv[2]))
        return _Proc()

    monkeypatch.setattr(lf.subprocess, "run", fake_run)
    report = lf.run_forge(out, n=4, seed=7, min_confidence=0.6)
    assert report["winner_path"] == str(out / "forge_winner.mid")
    assert captured["cwd"] == str(fake_libretto)
    # argv liste, pas de shell : les paramètres passent en arguments séparés
    assert captured["argv"][3:5] == ["4", "7"]
    assert "--min-confidence" in captured["argv"]


def test_run_forge_echec_process(fake_libretto: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(lf.subprocess, "run",
                        lambda *a, **k: _Proc(returncode=2, stderr="boom"))
    with pytest.raises(RuntimeError, match="Forge a échoué"):
        lf.run_forge(tmp_path / "out")


def test_run_forge_timeout(fake_libretto: Path, tmp_path: Path, monkeypatch):
    def boom(*a, **k):
        raise lf.subprocess.TimeoutExpired(cmd="forge", timeout=1)
    monkeypatch.setattr(lf.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="n'a pas fini"):
        lf.run_forge(tmp_path / "out", timeout=1)


def test_run_forge_sans_rapport(fake_libretto: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(lf.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError, match="n'a pas écrit"):
        lf.run_forge(tmp_path / "out")


# --------------------------------------------------------------------------- #
# Rôles (le cœur du mapping vers les gadgets)                                 #
# --------------------------------------------------------------------------- #


def _t(channel: int, mean: float) -> dict:
    return {"channel": channel, "mean_pitch": mean}


def test_roles_quatre_pistes():
    """Cas Forge réel : accords ch0, lead ch2, basse ch1, batterie ch9."""
    tracks = [_t(0, 66.0), _t(2, 72.5), _t(1, 42.0), _t(9, 41.2)]
    lf._assign_roles(tracks)
    assert [t["role"] for t in tracks] == ["chords", "lead", "bass", "drums"]


def test_roles_batterie_meme_si_grave():
    """Le canal 9 gagne sur la tessiture : une batterie grave reste batterie."""
    tracks = [_t(9, 38.0), _t(0, 60.0)]
    lf._assign_roles(tracks)
    assert tracks[0]["role"] == "drums" and tracks[1]["role"] == "lead"


def test_roles_piste_melodique_unique_est_lead():
    tracks = [_t(0, 45.0)]
    lf._assign_roles(tracks)
    assert tracks[0]["role"] == "lead"  # pas de « basse » sans contrepartie


def test_roles_que_des_percussions():
    tracks = [_t(9, 40.0)]
    lf._assign_roles(tracks)
    assert tracks[0]["role"] == "drums"


# --------------------------------------------------------------------------- #
# Catégories de gadgets → rôle                                                #
# --------------------------------------------------------------------------- #


_CATS = {"London": "Drum", "Madrid": "Bass", "Brussels": "Lead",
         "Marseille": "Keys", "Chicago": "Acid", "Zurich": "Recorder"}


def test_gadget_pour_role_prefere_la_categorie_typee():
    assert gs._gadget_for_role("drums", _CATS) == "London"
    assert gs._gadget_for_role("bass", _CATS) == "Madrid"
    assert gs._gadget_for_role("lead", _CATS) == "Brussels"
    assert gs._gadget_for_role("chords", _CATS) == "Marseille"


def test_gadget_pour_role_repli_categorie_suivante():
    """Sans « Bass » installé, la basse tombe sur « Acid » (2e préférence)."""
    cats = {k: v for k, v in _CATS.items() if v != "Bass"}
    assert gs._gadget_for_role("bass", cats) == "Chicago"


def test_gadget_pour_role_dernier_recours():
    assert gs._gadget_for_role("drums", {"Zurich": "Recorder"}) == "Zurich"
    assert gs._gadget_for_role("drums", {}) is None


# --------------------------------------------------------------------------- #
# Chaîne complète (Forge + Libretto + pont mockés)                            #
# --------------------------------------------------------------------------- #


_MIDI = {
    "tempo": 112.0,
    "markers": [{"position": 0.0, "name": "intro"}, {"position": 8.0, "name": "refrain"}],
    "total_notes": 3,
    "tracks": [
        {"source_track": 1, "channel": 0, "note_count": 2, "mean_pitch": 66.0,
         "role": "chords", "notes": [{"pitch": 60, "start": 0.0, "length": 0.5, "velocity": 90},
                                     {"pitch": 64, "start": 0.5, "length": 0.5, "velocity": 90}]},
        {"source_track": 4, "channel": 9, "note_count": 1, "mean_pitch": 38.0,
         "role": "drums", "notes": [{"pitch": 36, "start": 0.0, "length": 0.1, "velocity": 110}]},
    ],
}


class _FakeBridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._n = 0

    async def __call__(self, cmd: str, args: dict | None = None, timeout=None) -> dict:
        args = args or {}
        self.calls.append((cmd, args))
        if cmd == "add_track":
            self._n += 1
            return {"inserted_index": self._n - 1, "guid": f"{{G{self._n - 1}}}"}
        if cmd == "add_fx":
            return {"fx_index": 0, "fx_name": f"VSTi: {args['name']}", "guid": args.get("guid")}
        if cmd == "insert_midi_notes":
            return {"note_count": len(args.get("notes", []))}
        if cmd in ("set_tempo", "add_marker"):
            return {"ok": True}
        if cmd == "render_project":
            return {"rendered": True, "output_files": ["/tmp/x.wav"]}
        return {"error": f"cmd inattendue: {cmd}"}


@pytest.fixture
def chaine(monkeypatch):
    """Forge → gagnant → MIDI décodé → pont : tout mocké, rien d'externe."""
    bridge = _FakeBridge()
    monkeypatch.setattr(gs, "_bridge_call", bridge)
    monkeypatch.setattr(gs, "_gadget_categories", lambda: dict(_CATS))
    monkeypatch.setattr(gs, "_installed_gadgets", lambda: sorted(_CATS))
    monkeypatch.setattr(lf, "midi_to_tracks", lambda p: dict(_MIDI))
    return bridge


def _forge_ok(out_dir, *a, **k):
    report = _fake_report(Path(out_dir))
    report["winner_path"] = str(Path(out_dir) / "forge_winner.mid")
    return report


async def test_chaine_complete(chaine, monkeypatch):
    monkeypatch.setattr(lf, "run_forge", _forge_ok)
    r = await gs.forge_song_with_gadgets(n=4)
    assert r["tempo"] == 112.0
    assert [t["gadget"] for t in r["tracks"]] == ["Marseille", "London"]
    assert [t["role"] for t in r["tracks"]] == ["chords", "drums"]
    assert [t["category"] for t in r["tracks"]] == ["Keys", "Drum"]
    assert r["markers"] == 2
    assert "score SMS 0.83" in r["summary"]
    cmds = [c for c, _ in chaine.calls]
    assert cmds[0] == "set_tempo"
    assert cmds.count("insert_midi_notes") == 2
    assert cmds.count("add_marker") == 2


async def test_gate_bloque_avant_reaper(chaine, monkeypatch):
    """Aucun candidat fiable → RIEN ne part vers REAPER. Le cœur de l'outil."""
    def forge_sans_gagnant(out_dir, *a, **k):
        report = _fake_report(Path(out_dir), winner=False)
        report["winner_path"] = None
        return report

    monkeypatch.setattr(lf, "run_forge", forge_sans_gagnant)
    r = await gs.forge_song_with_gadgets(n=4, min_confidence=0.99)
    assert "gate" in r["error"]
    assert r["rejected_low_confidence"] == 4
    assert chaine.calls == []  # aucune mutation du projet REAPER


async def test_instruments_forces(chaine, monkeypatch):
    monkeypatch.setattr(lf, "run_forge", _forge_ok)
    r = await gs.forge_song_with_gadgets(n=4, instruments={"chords": "chicago"})
    assert r["tracks"][0]["gadget"] == "Chicago"  # casse ignorée
    assert r["tracks"][1]["gadget"] == "London"   # rôle non forcé → par catégorie


async def test_instrument_force_inconnu(chaine, monkeypatch):
    monkeypatch.setattr(lf, "run_forge", _forge_ok)
    r = await gs.forge_song_with_gadgets(n=4, instruments={"drums": "Zanzibar"})
    drums = next(t for t in r["tracks"] if t["role"] == "drums")
    assert drums["status"] == "missing" and "Zanzibar" in drums["error"]


async def test_notes_envoyees_par_lots(chaine, monkeypatch):
    """Le pont borne chaque ligne à 64 KiB → lots de NOTE_CHUNK notes."""
    gros = dict(_MIDI)
    gros["tracks"] = [{**_MIDI["tracks"][0],
                       "notes": [{"pitch": 60, "start": i * 0.1, "length": 0.1, "velocity": 90}
                                 for i in range(250)]}]
    monkeypatch.setattr(lf, "midi_to_tracks", lambda p: gros)
    monkeypatch.setattr(lf, "run_forge", _forge_ok)
    r = await gs.forge_song_with_gadgets(n=4)
    inserts = [a for c, a in chaine.calls if c == "insert_midi_notes"]
    assert len(inserts) == 3  # 120 + 120 + 10
    assert [len(a["notes"]) for a in inserts] == [120, 120, 10]
    assert r["tracks"][0]["notes"] == 250


async def test_forge_indisponible_message_actionnable(monkeypatch):
    def absent(*a, **k):
        raise lf.LibrettoUnavailable("Libretto introuvable sous /nulle/part")
    monkeypatch.setattr(lf, "run_forge", absent)
    r = await gs.forge_song_with_gadgets()
    assert "Libretto introuvable" in r["error"]


async def test_analyze_midi_structure_refuse_non_midi(tmp_path: Path):
    txt = tmp_path / "notes.txt"
    txt.write_text("pas du midi")
    r = await gs.analyze_midi_structure(str(txt))
    assert "error" in r


async def test_analyze_midi_structure_sans_libretto(tmp_path: Path, monkeypatch):
    mid = tmp_path / "x.mid"
    mid.write_bytes(b"MThd")
    monkeypatch.setenv("LIBRETTO_ROOT", str(tmp_path / "vide"))
    r = await gs.analyze_midi_structure(str(mid))
    assert "Libretto introuvable" in r["error"]

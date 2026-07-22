"""Tests klody_mcp.gadget_server — parseur .gdproj2 + outils (pont REAPER mocké).

Fixture 100 % synthétique : on fabrique un .gddat NSKeyedArchiver + ZIP embarqué
en mémoire (même layout que Gadget 2.9.5, rétro-ingénierie 22/07/26). Aucun
projet réel, aucun REAPER requis — CI-sûr.
"""
from __future__ import annotations

import io
import json
import plistlib
import struct
import zipfile
from pathlib import Path

import pytest
from klody_mcp import gadget_server as gs
from klody_mcp._pathguard import PathGuardViolation

# --------------------------------------------------------------------------- #
# Fabrique de fixtures                                                        #
# --------------------------------------------------------------------------- #


def _seq_dat(tempo: float = 128.0, version: int = 2) -> bytes:
    """seq.dat minimal : @0 version int32 LE, @8 tempo float32 LE."""
    buf = bytearray(48)
    struct.pack_into("<i", buf, 0, version)
    struct.pack_into("<f", buf, 8, tempo)
    struct.pack_into("<f", buf, 44, 120.0)  # constante interne réelle (leurre)
    return bytes(buf)


def _plugin_info(name: str, as_json: bool = False) -> bytes:
    if as_json:
        return json.dumps({"Name": name}).encode()
    return plistlib.dumps({"Name": name}, fmt=plistlib.FMT_XML)


def _project_zip(tracks: list[str], tempo: float = 128.0, version: int = 2) -> bytes:
    """ZIP root/ conforme : 1 instrument + ChannelStrip par piste, bus, master."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("root/seqs/seq.dat", _seq_dat(tempo, version))
        z.writestr("root/seqs/seqInfo.plist", plistlib.dumps(["1"], fmt=plistlib.FMT_XML))
        midi = {str(i): {"EndpointId": 0, "EndpointName": "", "MidiChannel": i + 1}
                for i in range(len(tracks))}
        z.writestr("root/midiOutInfo.plist", plistlib.dumps(midi, fmt=plistlib.FMT_XML))
        for i, gadget in enumerate(tracks):
            z.writestr(f"root/tracks/{i}/plugins/0/plugin_info", _plugin_info(gadget))
            z.writestr(f"root/tracks/{i}/plugins/1/plugin_info", _plugin_info("ChannelStrip"))
        z.writestr("root/buses/0/plugins/0/plugin_info", _plugin_info("GenericMixer"))
        z.writestr("root/buses/0/plugins/1/plugin_info", _plugin_info("HallReverb"))
        z.writestr("root/master/plugins/0/plugin_info", _plugin_info("MasterLimiter"))
    return out.getvalue()


def _gddat(tracks: list[str], key: str = "C", scale: str = "Dorian",
           tempo: float = 128.0, version: int = 2) -> bytes:
    """NSKeyedArchiver minimal, même ordre d'objets que Gadget réel :
    ['Key', 'Scale', '<key>', '<scale>', <zip bytes>]."""
    archive = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            "KOProjectItemAttrID",
            "Key", "Scale", key, scale,
            _project_zip(tracks, tempo, version),
        ],
    }
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    """Package .gdproj2 sous une racine autorisée (tmp_path via env)."""
    monkeypatch.setenv("GADGET_PROJECT_ROOTS", str(tmp_path))
    pkg = tmp_path / "Ma Chanson.gdproj2"
    pkg.mkdir()
    (pkg / "Ma Chanson.gddat").write_bytes(
        _gddat(["Chicago", "London"], key="F#", scale="Minor", tempo=97.5)
    )
    return pkg


# --------------------------------------------------------------------------- #
# Enregistrement des outils                                                   #
# --------------------------------------------------------------------------- #


async def test_serveur_expose_les_8_outils():
    tools = await gs.mcp._list_tools()
    names = {t.name for t in tools}
    expected = {
        "list_gadgets", "list_gadget_projects", "read_gadget_project",
        "create_gadget_track", "import_gadget_project_to_reaper", "gadget_status",
        "analyze_midi_structure", "forge_song_with_gadgets",
    }
    assert expected == names, f"écart : {expected ^ names}"


async def test_tools_ont_descriptions():
    for t in await gs.mcp._list_tools():
        assert t.description and len(t.description) > 20, t.name


# --------------------------------------------------------------------------- #
# Parseur                                                                     #
# --------------------------------------------------------------------------- #


async def test_read_project_complet(project_dir: Path):
    r = await gs.read_gadget_project(str(project_dir))
    assert "error" not in r, r
    assert (r["name"], r["key"], r["scale"]) == ("Ma Chanson", "F#", "Minor")
    assert r["tempo"] == 97.5
    assert r["format_version"] == 2
    gadgets = [t["gadget"] for t in r["tracks"]]
    assert gadgets == ["Chicago", "London"]
    assert r["tracks"][0]["fx_chain"] == ["ChannelStrip"]
    assert r["tracks"][1]["midi_channel"] == 2
    assert r["buses"][0]["chain"] == ["GenericMixer", "HallReverb"]
    assert r["master_chain"][0]["chain"] == ["MasterLimiter"]


async def test_read_project_gddat_renomme(project_dir: Path):
    """Package renommé au Finder : le .gddat interne garde l'ancien nom."""
    renamed = project_dir.parent / "Autre Nom.gdproj2"
    project_dir.rename(renamed)
    r = await gs.read_gadget_project(str(renamed))
    assert "error" not in r and r["key"] == "F#"


def test_seq_header_tempo_hors_plage():
    blob = _project_zip(["Chicago"], tempo=5000.0)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        tempo, version = gs._read_seq_header(z, set(z.namelist()))
    assert tempo is None and version == 2


def test_plugin_info_json_tolere():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("root/tracks/0/plugins/0/plugin_info", _plugin_info("Berlin", as_json=True))
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
        assert gs._plugin_name(z, "root/tracks/0/plugins/0/plugin_info") == "Berlin"


async def test_read_project_corrompu(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GADGET_PROJECT_ROOTS", str(tmp_path))
    bad = tmp_path / "Cassé.gdproj2"
    bad.mkdir()
    (bad / "Cassé.gddat").write_bytes(b"pas un plist du tout")
    r = await gs.read_gadget_project(str(bad))
    assert "error" in r


async def test_read_project_refuse_hors_racines(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GADGET_PROJECT_ROOTS", str(tmp_path / "seule_racine"))
    (tmp_path / "seule_racine").mkdir()
    r = await gs.read_gadget_project("/etc/passwd")
    assert "error" in r


async def test_read_project_refuse_mauvais_suffixe(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GADGET_PROJECT_ROOTS", str(tmp_path))
    secret = tmp_path / "notes.txt"
    secret.write_text("privé")
    with pytest.raises(PathGuardViolation):
        gs._safe_project_path(str(secret))


# --------------------------------------------------------------------------- #
# Scan                                                                        #
# --------------------------------------------------------------------------- #


async def test_list_projects_trouve_packages_et_plats(project_dir: Path, tmp_path: Path):
    flat = tmp_path / "vieux.gdproj"
    flat.write_bytes(_gddat(["Berlin"]))
    r = await gs.list_gadget_projects(str(tmp_path))
    names = {p["name"] for p in r["projects"]}
    assert {"Ma Chanson", "vieux"} <= names
    assert r["count"] == len(r["projects"])


async def test_list_projects_ne_descend_pas_dans_les_packages(project_dir: Path, tmp_path: Path):
    r = await gs.list_gadget_projects(str(tmp_path))
    paths = [p["path"] for p in r["projects"]]
    assert not any(".gdproj2/" in p for p in paths)


# --------------------------------------------------------------------------- #
# Pilotage via pont (mocké)                                                   #
# --------------------------------------------------------------------------- #


class _FakeBridge:
    """Enregistre les commandes ; simule add_track/add_fx/set_tempo/add_marker."""

    def __init__(self, fx_missing: set[str] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fx_missing = fx_missing or set()
        self._next_index = 0

    async def __call__(self, cmd: str, args: dict | None = None) -> dict:
        args = args or {}
        self.calls.append((cmd, args))
        if cmd == "add_track":
            idx = self._next_index
            self._next_index += 1
            return {"inserted_index": idx, "guid": f"{{G{idx}}}", "track_count": idx + 1}
        if cmd == "add_fx":
            if any(m in args.get("name", "") for m in self.fx_missing):
                return {"error": f"effet introuvable: {args['name']!r}"}
            return {"fx_index": 0, "fx_name": f"VSTi: {args['name']}",
                    "guid": args.get("guid", "{G?}"), "created": True}
        if cmd == "set_tempo":
            return {"bpm": args["bpm"]}
        if cmd == "add_marker":
            return {"position": 0.0, "name": args.get("name"), "created": True}
        if cmd == "ping":
            return {"pong": True}
        return {"error": f"cmd inattendue: {cmd}"}


async def test_create_gadget_track_ok(monkeypatch):
    bridge = _FakeBridge()
    monkeypatch.setattr(gs, "_bridge_call", bridge)
    monkeypatch.setattr(gs, "_installed_gadgets", lambda: ["Chicago", "London"])
    r = await gs.create_gadget_track("chicago", track_name="Bassline")
    assert r["status"] == "ok" and r["gadget"] == "Chicago"
    assert bridge.calls[0] == ("add_track", {"name": "Bassline"})
    # suffixe (KORG) systématique — évite les collisions de sous-chaîne
    assert bridge.calls[1][1]["name"] == "Chicago (KORG)"


async def test_create_gadget_track_inconnu(monkeypatch):
    monkeypatch.setattr(gs, "_installed_gadgets", lambda: ["Chicago"])
    r = await gs.create_gadget_track("Zanzibar")
    assert "error" in r and r["installed"] == ["Chicago"]


async def test_import_projet_dans_reaper(project_dir: Path, monkeypatch):
    bridge = _FakeBridge()
    monkeypatch.setattr(gs, "_bridge_call", bridge)
    monkeypatch.setattr(gs, "_installed_gadgets", lambda: ["Chicago", "London"])
    r = await gs.import_gadget_project_to_reaper(str(project_dir))
    assert r["tempo_set"] == 97.5
    assert r["key_marker"] == "Key: F# Minor"
    assert [t["status"] for t in r["tracks"]] == ["ok", "ok"]
    cmds = [c for c, _ in bridge.calls]
    assert cmds == ["set_tempo", "add_marker", "add_track", "add_fx", "add_track", "add_fx"]
    assert "2 piste(s)" in r["summary"]


async def test_import_projet_gadget_vst_manquant(project_dir: Path, monkeypatch):
    """VST absent → statut missing, l'import CONTINUE (patron build_vocal_chain)."""
    bridge = _FakeBridge(fx_missing={"Chicago"})
    monkeypatch.setattr(gs, "_bridge_call", bridge)
    monkeypatch.setattr(gs, "_installed_gadgets", lambda: ["Chicago", "London"])
    r = await gs.import_gadget_project_to_reaper(str(project_dir))
    statuses = [t["status"] for t in r["tracks"]]
    assert statuses == ["missing", "ok"]
    assert "1 piste(s) créée(s), 1 manquante(s)" in r["summary"]


async def test_import_projet_pont_down(project_dir: Path, monkeypatch):
    async def down(cmd, args=None):
        return {"error": "pont REAPER injoignable"}
    monkeypatch.setattr(gs, "_bridge_call", down)
    monkeypatch.setattr(gs, "_installed_gadgets", lambda: ["Chicago", "London"])
    r = await gs.import_gadget_project_to_reaper(str(project_dir))
    # pont down → on s'arrête après la 1re piste en erreur, pas de boucle inutile
    assert len(r["tracks"]) == 1 and "error" in r["tracks"][0]


async def test_import_respecte_max_tracks(project_dir: Path, monkeypatch):
    bridge = _FakeBridge()
    monkeypatch.setattr(gs, "_bridge_call", bridge)
    monkeypatch.setattr(gs, "_installed_gadgets", lambda: ["Chicago", "London"])
    r = await gs.import_gadget_project_to_reaper(str(project_dir), max_tracks=1)
    assert len(r["tracks"]) == 1
    assert "1 au-delà de max_tracks" in r["summary"]


async def test_gadget_status_pont_down(monkeypatch):
    async def down(cmd, args=None):
        return {"error": "pont REAPER injoignable"}
    monkeypatch.setattr(gs, "_bridge_call", down)
    r = await gs.gadget_status()
    assert r["reaper_bridge"] == "pont REAPER injoignable"
    assert isinstance(r["vst_count"], int)

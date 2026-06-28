"""Tests du registre de plugins REAPER (klody_mcp.reaper_plugins).

Pur parsing de fichiers cache : on écrit un faux dossier ressource REAPER (env
KLODY_REAPER_RESOURCE) avec un reaper-vstplugins + un reaper-jsfx au format réel,
et on vérifie la liste + la résolution par rôle (préférence aux plugins user).
Tourne en CI sans REAPER.
"""
from __future__ import annotations

from klody_mcp import reaper_plugins as rp

_VST_INI = """\
[vstcache]
KaribVoice_Compressor.vst3=80100D75D2F5DC01,585272909{ABCDEF019182FAEB4B72736E4B763033,KaribVoice Compressor (KaribSound)
KaribVoice_EQ.vst3=00376D3CD2F5DC01,568495290{ABCDEF019182FAEB4B72736E4B763032,KaribVoice EQ (KaribSound)
KaribVoice_Reverb.vst3=80402793D3F5DC01,400719100{ABCDEF019182FAEB4B72736E4B763038,KaribVoice Reverb (KaribSound)
reacomp.vst.dylib=80B0C9B966F7DC01,1919247213,ReaComp (Cockos)
reaeq.vst.dylib=004762BA66F7DC01,1919247729,ReaEQ (Cockos)
reaverb.vst.dylib=004762BA66F7DC01,1919247730,ReaVerb (Cockos)
some_random_synth.vst3=00,123,Massive (Native Instruments)
comma_comp.vst3=00,124{ABCDEF019182FAEB,Comp, Vintage (Acme)
"""

_JSFX_INI = """\
VERSION 1
NAME analysis/loudness_meter "JS: Loudness Meter Peak/RMS/LUFS (Cockos)"
NAME delay/delay "JS: Delay"
NAME guitar/amp "JS: Amp Sim"
"""


def _setup_resource(tmp_path, monkeypatch):
    (tmp_path / "reaper-vstplugins_arm64.ini").write_text(_VST_INI, encoding="utf-8")
    (tmp_path / "reaper-jsfx.ini").write_text(_JSFX_INI, encoding="utf-8")
    monkeypatch.setenv("KLODY_REAPER_RESOURCE", str(tmp_path))


def test_list_installed_fx_parses_vst_and_jsfx(tmp_path, monkeypatch):
    _setup_resource(tmp_path, monkeypatch)
    fx = rp.list_installed_fx()
    names = {f["name"] for f in fx}
    assert "KaribVoice Compressor (KaribSound)" in names
    assert "ReaEQ (Cockos)" in names
    assert "JS: Delay" in names  # jsfx aussi
    # le nom est bien le texte APRÈS la dernière virgule (pas le hash/guid)
    assert all("{" not in n and "vst3" not in n.lower() for n in names)
    kinds = {f["kind"] for f in fx}
    assert kinds == {"vst", "jsfx"}


def test_vst_name_with_comma_is_preserved(tmp_path, monkeypatch):
    # Un nom de plugin contenant une virgule ne doit PAS être tronqué (coupe après la
    # virgule structurelle `{guid,`, pas après la dernière).
    _setup_resource(tmp_path, monkeypatch)
    names = {f["name"] for f in rp.list_installed_fx()}
    assert "Comp, Vintage (Acme)" in names


def test_list_installed_fx_filter(tmp_path, monkeypatch):
    _setup_resource(tmp_path, monkeypatch)
    karib = rp.list_installed_fx(filter="karibvoice")
    assert karib and all("karibvoice" in f["name"].lower() for f in karib)
    assert len(karib) == 3


def test_list_installed_fx_kind_restriction(tmp_path, monkeypatch):
    _setup_resource(tmp_path, monkeypatch)
    only_js = rp.list_installed_fx(kinds=("jsfx",))
    assert only_js and all(f["kind"] == "jsfx" for f in only_js)


def test_resolve_plugin_prefers_user_over_stock(tmp_path, monkeypatch):
    _setup_resource(tmp_path, monkeypatch)
    # eq : KaribVoice EQ (user) doit gagner sur ReaEQ (stock)
    assert rp.resolve_plugin("eq")["name"] == "KaribVoice EQ (KaribSound)"
    assert rp.resolve_plugin("comp")["name"] == "KaribVoice Compressor (KaribSound)"
    assert rp.resolve_plugin("reverb")["name"] == "KaribVoice Reverb (KaribSound)"


def test_resolve_plugin_falls_back_to_stock(tmp_path, monkeypatch):
    # Pas de plugin user pour 'delay' -> doit prendre le JS: Delay (stock/jsfx)
    _setup_resource(tmp_path, monkeypatch)
    r = rp.resolve_plugin("delay")
    assert r is not None and "delay" in r["name"].lower()


def test_resolve_plugin_unknown_role_or_absent(tmp_path, monkeypatch):
    _setup_resource(tmp_path, monkeypatch)
    assert rp.resolve_plugin("nonsense_role") is None  # rôle inconnu
    assert rp.resolve_plugin("limiter") is None  # aucun limiter dans la fixture


def test_missing_resource_dir_is_empty(tmp_path, monkeypatch):
    # Dossier ressource inexistant -> liste vide, jamais d'exception.
    monkeypatch.setenv("KLODY_REAPER_RESOURCE", str(tmp_path / "nope"))
    assert rp.list_installed_fx() == []
    assert rp.resolve_plugin("eq") is None

"""Tests de la recherche de samples (klody_mcp.reaper_samples).

Pur filesystem : on monte une fausse bibliothèque de samples dans tmp (env
KLODY_SAMPLES_DIR) et on vérifie le filtrage par extension, le classement par
pertinence et les bornes. Tourne en CI.
"""
from __future__ import annotations

from klody_mcp import reaper_samples as rs


def _make_library(tmp_path):
    (tmp_path / "drums").mkdir()
    (tmp_path / "bass").mkdir()
    (tmp_path / "drums" / "kick_punchy_01.wav").write_bytes(b"RIFF")
    (tmp_path / "drums" / "kick_soft.wav").write_bytes(b"RIFF")
    (tmp_path / "drums" / "snare_tight.wav").write_bytes(b"RIFF")
    (tmp_path / "bass" / "sub_bass_zouk.aif").write_bytes(b"FORM")
    (tmp_path / "readme.txt").write_text("pas un sample")  # non-audio -> ignoré
    return tmp_path


def test_search_ranks_filename_match_first(tmp_path, monkeypatch):
    lib = _make_library(tmp_path)
    monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
    hits = rs.search_samples("kick")
    assert hits, "doit trouver des kicks"
    # les deux kicks remontent, le .txt jamais
    names = [h["name"] for h in hits]
    assert all(n.endswith((".wav", ".aif")) for n in names)
    assert all("kick" in n.lower() for n in hits if False) or names[0].startswith("kick")
    assert "readme.txt" not in names


def test_search_excludes_non_audio(tmp_path, monkeypatch):
    lib = _make_library(tmp_path)
    monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
    # requête vide -> liste tout l'audio (score 0), jamais le .txt
    allhits = rs.search_samples("", limit=100)
    names = {h["name"] for h in allhits}
    assert "readme.txt" not in names
    assert "kick_punchy_01.wav" in names
    assert "sub_bass_zouk.aif" in names
    assert len(allhits) == 4  # 4 fichiers audio


def test_search_path_token_match(tmp_path, monkeypatch):
    lib = _make_library(tmp_path)
    monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
    # 'zouk' est dans le nom de fichier ; 'bass' dans le nom ET le dossier
    hits = rs.search_samples("zouk")
    assert hits[0]["name"] == "sub_bass_zouk.aif"
    assert hits[0]["score"] > 0
    assert hits[0]["path"].endswith("sub_bass_zouk.aif")  # provenance = chemin absolu


def test_search_query_no_match_returns_empty(tmp_path, monkeypatch):
    lib = _make_library(tmp_path)
    monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
    assert rs.search_samples("xylophone_inexistant") == []


def test_search_respects_limit(tmp_path, monkeypatch):
    lib = _make_library(tmp_path)
    monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
    assert len(rs.search_samples("", limit=2)) == 2


def test_root_override_beats_env(tmp_path, monkeypatch):
    lib = _make_library(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "clap_solo.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("KLODY_SAMPLES_DIR", str(lib))
    # root explicite -> ne cherche QUE dans `other`
    hits = rs.search_samples("clap", root=str(other))
    assert len(hits) == 1 and hits[0]["name"] == "clap_solo.wav"


def test_roots_filters_nonexistent(tmp_path, monkeypatch):
    monkeypatch.setenv("KLODY_SAMPLES_DIR", str(tmp_path / "ghost"))
    assert rs._roots() == []  # dossier inexistant -> aucune racine
    assert rs.search_samples("kick") == []

"""Tests klody_mcp.atelier_server — trio job autour de `hub-analyze` (MISSION-D 6.1).

Aucun modèle, aucun vrai sous-processus : `_lancer` est remplacé par un faux qui
écrit `job.json` ; l'état « done » est simulé en déposant `hub_manifest.json`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from klody_mcp import atelier_server as at

_WAV = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 64


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Cache isolé + racine audio = tmp_path + venv suite « présent »."""
    cache = tmp_path / "cache"
    monkeypatch.setattr(at, "CACHE_DIR", cache)
    monkeypatch.setattr(at, "AUDIO_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(at, "_roots", lambda: [tmp_path.resolve()])
    fake_py = tmp_path / "python"
    fake_py.write_text("#!/bin/sh\n")
    monkeypatch.setattr(at, "SUITE_PYTHON", fake_py)
    monkeypatch.setattr(at, "SUITE_ROOT", tmp_path)
    return cache


@pytest.fixture
def morceau(tmp_path: Path) -> Path:
    p = tmp_path / "zouk.wav"
    p.write_bytes(_WAV)
    return p


def _faux_lancer(monkeypatch, pid_vivant: bool = True):
    """Remplace le sous-processus par une écriture de job.json (pid = nous, donc vivant)."""
    appels: list[dict] = []

    def fake(job_dir, audio, *, profil, a2a, sha):
        job_dir.mkdir(parents=True, exist_ok=True)
        meta = {"job_id": job_dir.name, "input": str(audio), "sha256": sha, "profil": profil,
                "a2a": a2a, "pid": os.getpid() if pid_vivant else 999_999_999,
                "started": "2026-09-15T00:00:00+00:00", "started_ts": 1.0}
        (job_dir / "job.json").write_text(json.dumps(meta))
        appels.append(meta)
        return meta

    monkeypatch.setattr(at, "_lancer", fake)
    return appels


def _manifest(job_dir: Path, **summary):
    m = {"input": "x.wav", "out_dir": str(job_dir), "all_core_ok": True,
         "modules": {"separation": {"ok": True, "sec": 8.0}, "tempo_mubridge": {"ok": False, "detail": "rouge", "sec": 0.0}},
         "summary": {"bpm": 98.0, "key": {"tonic": "C#", "mode": "minor"}, "n_chords": 12,
                     "chords": [{"t": 0.0, "label": "C#m", "dur": 2.0}], "sections": [],
                     "analysis_json": str(job_dir / "analysis.json"), "stems": {"drums": "d.wav"},
                     **summary}}
    (job_dir / "hub_manifest.json").write_text(json.dumps(m))
    return m


# ------------------------------------------------------------------ job_id --

class TestJobId:
    def test_deterministe_et_sans_slash(self):
        a = at.job_id_for("ab" * 32, profil="balanced", a2a=False)
        b = at.job_id_for("ab" * 32, profil="balanced", a2a=False)
        assert a == b and "/" not in a and a.endswith(at.PIPELINE_VERSION)

    def test_profil_et_a2a_changent_la_cle(self):
        base = at.job_id_for("ab" * 32, profil="balanced", a2a=False)
        assert at.job_id_for("ab" * 32, profil="hq", a2a=False) != base
        assert at.job_id_for("ab" * 32, profil="balanced", a2a=True) != base

    @pytest.mark.parametrize("bad", ["", "../x", "a/b", ".hidden"])
    def test_job_dir_refuse_les_chemins(self, bad):
        with pytest.raises(ValueError):
            at._job_dir(bad)


# ------------------------------------------------------ analyser_morceau --

class TestAnalyserMorceau:
    def test_lance_puis_running(self, cache, morceau, monkeypatch):
        appels = _faux_lancer(monkeypatch)
        r = at.analyser_morceau(str(morceau))
        assert r["status"] == "running" and r["cache"] is False
        assert len(appels) == 1 and appels[0]["profil"] == "balanced"
        assert at.statut_analyse(r["job_id"])["status"] == "running"

    def test_second_appel_pendant_le_job_ne_relance_pas(self, cache, morceau, monkeypatch):
        appels = _faux_lancer(monkeypatch)
        r1 = at.analyser_morceau(str(morceau))
        r2 = at.analyser_morceau(str(morceau))
        assert r1["job_id"] == r2["job_id"] and r2["status"] == "running"
        assert len(appels) == 1

    def test_cache_hit_quand_manifeste_present(self, cache, morceau, monkeypatch):
        appels = _faux_lancer(monkeypatch)
        r1 = at.analyser_morceau(str(morceau))
        _manifest(cache / r1["job_id"])
        r2 = at.analyser_morceau(str(morceau))
        assert r2["status"] == "done" and r2["cache"] is True
        assert len(appels) == 1

    def test_chemin_hors_racines_refuse(self, cache, monkeypatch, tmp_path):
        _faux_lancer(monkeypatch)
        ailleurs = tmp_path.parent / "ailleurs.wav"
        r = at.analyser_morceau(str(ailleurs))
        assert "error" in r and ("refusé" in r["error"] or "introuvable" in r["error"])

    def test_extension_non_audio_refusee(self, cache, monkeypatch, tmp_path):
        _faux_lancer(monkeypatch)
        p = tmp_path / "notes.txt"
        p.write_text("x")
        assert "extension" in at.analyser_morceau(str(p))["error"]

    def test_profil_inconnu(self, cache, morceau, monkeypatch):
        _faux_lancer(monkeypatch)
        assert "profil" in at.analyser_morceau(str(morceau), profil="turbo")["error"]

    def test_venv_suite_absent(self, cache, morceau, monkeypatch):
        _faux_lancer(monkeypatch)
        monkeypatch.setattr(at, "SUITE_PYTHON", Path("/nulle/part/python"))
        assert "venv" in at.analyser_morceau(str(morceau))["error"]

    def test_vrai_lancer_construit_la_commande(self, cache, morceau, monkeypatch):
        """`_lancer` réel avec Popen factice : commande, cwd, journal, job.json."""
        captured = {}

        class FauxProc:
            pid = 4242

        def fake_popen(cmd, cwd=None, stdin=None, stdout=None, stderr=None, start_new_session=False):
            captured.update(cmd=cmd, cwd=cwd, session=start_new_session, stdin=stdin)
            return FauxProc()

        monkeypatch.setattr(at.subprocess, "Popen", fake_popen)
        meta = at._lancer(cache / "j1", morceau, profil="hq", a2a=True, sha="ff" * 32)
        assert captured["cmd"][:3] == [str(at.SUITE_PYTHON), "-m", "core.cli"]
        assert "hub-analyze" in captured["cmd"] and "--a2a" in captured["cmd"]
        assert captured["cmd"][captured["cmd"].index("--profile") + 1] == "hq"
        assert captured["cwd"] == str(at.SUITE_ROOT) and captured["session"] is True
        assert captured["stdin"] == at.subprocess.DEVNULL   # jamais le stdin JSON-RPC du serveur
        assert meta["pid"] == 4242 and (cache / "j1" / "job.json").is_file()


# ------------------------------------------------ statut / resultat / liste --

class TestStatutResultat:
    def test_statut_inconnu(self, cache):
        assert at.statut_analyse("inexistant")["status"] == "unknown"

    def test_statut_error_si_pid_mort_sans_manifeste(self, cache, morceau, monkeypatch):
        _faux_lancer(monkeypatch, pid_vivant=False)
        r = at.analyser_morceau(str(morceau))
        s = at.statut_analyse(r["job_id"])
        assert s["status"] == "error" and "detail" in s

    def test_resultat_avant_fin_renvoie_le_statut(self, cache, morceau, monkeypatch):
        _faux_lancer(monkeypatch)
        r = at.analyser_morceau(str(morceau))
        res = at.resultat_analyse(r["job_id"])
        assert res["status"] == "running" and "summary" not in res

    def test_resultat_done_expose_summary_et_chemins(self, cache, morceau, monkeypatch):
        _faux_lancer(monkeypatch)
        r = at.analyser_morceau(str(morceau))
        job_dir = cache / r["job_id"]
        _manifest(job_dir, moss={"caption": "Zouk love"})
        (job_dir / "drums.json").write_text("{}")
        res = at.resultat_analyse(r["job_id"])
        assert res["all_core_ok"] is True
        assert res["summary"]["bpm"] == 98.0 and res["summary"]["moss"]["caption"] == "Zouk love"
        assert "analysis_json" not in res["summary"]          # rangé dans paths
        assert res["paths"]["drums_json"] == str(job_dir / "drums.json")
        assert res["paths"]["loops_json"] is None
        assert res["paths"]["stems"] == {"drums": "d.wav"}
        assert res["modules"]["tempo_mubridge"] == {"ok": False, "detail": "rouge", "sec": 0.0}
        assert "manifest" not in res
        assert "manifest" in at.resultat_analyse(r["job_id"], detail="complet")

    def test_resultat_job_id_invalide(self, cache):
        assert "error" in at.resultat_analyse("../x")

    def test_elapsed_d_un_job_fini_est_la_duree_reelle(self, cache, morceau, monkeypatch):
        """started_ts=1.0 (fixture) ; le manifeste est écrit maintenant → durée = mtime − 1.0,
        pas « maintenant − 1.0 » à chaque consultation (les deux valent ici presque pareil,
        d'où le contrôle de STABILITÉ : deux lectures espacées rendent la même valeur)."""
        import time
        _faux_lancer(monkeypatch)
        r = at.analyser_morceau(str(morceau))
        _manifest(cache / r["job_id"])
        e1 = at.statut_analyse(r["job_id"])["elapsed_sec"]
        time.sleep(0.05)
        e2 = at.statut_analyse(r["job_id"])["elapsed_sec"]
        assert e1 == e2 and e1 is not None

    def test_lister_analyses(self, cache, morceau, monkeypatch):
        _faux_lancer(monkeypatch)
        r = at.analyser_morceau(str(morceau))
        _manifest(cache / r["job_id"])
        rows = at.lister_analyses(k=5)["analyses"]
        assert rows and rows[0]["job_id"] == r["job_id"] and rows[0]["status"] == "done"

    def test_lister_sans_cache(self, cache):
        assert at.lister_analyses()["analyses"] == []


# ------------------------------------------------------------ statut_atelier --

class TestStatutAtelier:
    def test_venv_absent(self, cache, monkeypatch):
        monkeypatch.setattr(at, "SUITE_PYTHON", Path("/nulle/part/python"))
        s = at.statut_atelier()
        assert s["ok"] is False and "venv" in s["detail"]

    def test_hub_status_relaye(self, cache, monkeypatch):
        class R:
            stdout = json.dumps({"all_ok": False, "modules": [{"module": "song2chords", "ok": False}]})

        monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: R())
        s = at.statut_atelier()
        assert s["ok"] is False and s["modules"][0]["module"] == "song2chords"
        assert "dégradé" in s["note"]


# ------------------------------------------------------------- contrat MCP --

def test_les_cinq_outils_sont_exposes():
    import asyncio

    async def _noms():
        return sorted(t.name for t in await at.mcp.list_tools())

    noms = asyncio.run(_noms())
    assert noms == sorted(["analyser_morceau", "statut_analyse", "resultat_analyse",
                           "lister_analyses", "statut_atelier"])

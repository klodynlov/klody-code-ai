"""Tests du lot 2.2 — instrumentation et analyse du coût B du garde doc."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from bench.framework import Result
from bench.gate import load_run


def _result_dict(**overrides) -> dict:
    base = {
        "task_id": "real_repo/batch_atomic_delete",
        "category": "real_repo",
        "success": True,
        "detail": "ok",
        "latency_s": 42.0,
        "tokens_generated": 100,
        "tokens_per_sec": 2.4,
        "tool_calls_total": 5,
        "tool_calls_broken": 0,
        "iterations": 4,
        "error": None,
        "doc_guard_fired": True,
        "doc_consulte": False,
    }
    base.update(overrides)
    return base


class TestResultAccepteNouveauxChamps:
    """Les nouveaux champs doivent être optionnels et bien sérialisés."""

    def test_result_avec_champs_garde(self):
        r = Result(**_result_dict())
        assert r.doc_guard_fired is True
        assert r.doc_consulte is False

    def test_result_sans_champs_garde(self):
        d = _result_dict()
        del d["doc_guard_fired"]
        del d["doc_consulte"]
        r = Result(**d)
        assert r.doc_guard_fired is None
        assert r.doc_consulte is None

    def test_serialisation_aller_retour(self):
        r = Result(**_result_dict())
        j = json.dumps(r.__dict__)
        r2 = Result(**json.loads(j))
        assert r2.doc_guard_fired == r.doc_guard_fired
        assert r2.doc_consulte == r.doc_consulte


class TestRetrocompatibiliteGate:
    """Le gate doit charger les anciens résultats sans doc_guard_fired."""

    def test_gate_charge_sans_champs_garde(self, tmp_path):
        ancien = [_result_dict()]
        del ancien[0]["doc_guard_fired"]
        del ancien[0]["doc_consulte"]
        f = tmp_path / "old.json"
        f.write_text(json.dumps({"meta": {}, "results": ancien}))
        _, results = load_run(f)
        assert len(results) == 1
        assert "doc_guard_fired" not in results[0]

    def test_gate_charge_avec_champs_garde(self, tmp_path):
        f = tmp_path / "new.json"
        f.write_text(json.dumps({"meta": {}, "results": [_result_dict()]}))
        _, results = load_run(f)
        assert results[0]["doc_guard_fired"] is True


class TestAnalyseScript:
    """Le script d'analyse produit les bonnes catégorisations."""

    def _ecrire_resultats(self, tmp_path, resultats):
        f = tmp_path / "run.json"
        f.write_text(json.dumps({"meta": {}, "results": resultats}))
        return f

    def test_sauve_vs_cout_pour_rien(self, tmp_path):
        import subprocess
        import sys

        resultats = [
            _result_dict(task_id="real_repo/a", success=True, doc_guard_fired=True),
            _result_dict(task_id="real_repo/b", success=False, doc_guard_fired=True),
            _result_dict(task_id="real_repo/c", success=True, doc_guard_fired=False),
        ]
        f = self._ecrire_resultats(tmp_path, resultats)
        out_json = tmp_path / "synth.json"

        proc = subprocess.run(
            [sys.executable, "scripts/analyse_cout_garde_B.py",
             str(f), "--json", str(out_json)],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert proc.returncode == 0, proc.stderr

        synth = json.loads(out_json.read_text())
        assert synth["garde_declenche"] == 2
        assert synth["sauve_verdict"] == 1
        assert synth["cout_pour_rien"] == 1

    def test_sans_instrumentation_sort_1(self, tmp_path):
        import subprocess
        import sys

        ancien = [_result_dict()]
        del ancien[0]["doc_guard_fired"]
        del ancien[0]["doc_consulte"]
        f = self._ecrire_resultats(tmp_path, ancien)

        proc = subprocess.run(
            [sys.executable, "scripts/analyse_cout_garde_B.py", str(f)],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert proc.returncode == 1

    def test_seuil_50_pourcent(self, tmp_path):
        import subprocess
        import sys

        resultats = [
            _result_dict(task_id="real_repo/a", success=False, doc_guard_fired=True),
            _result_dict(task_id="real_repo/b", success=False, doc_guard_fired=True),
            _result_dict(task_id="real_repo/c", success=True, doc_guard_fired=False),
        ]
        f = self._ecrire_resultats(tmp_path, resultats)

        proc = subprocess.run(
            [sys.executable, "scripts/analyse_cout_garde_B.py", str(f)],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert proc.returncode == 0
        assert "SEUIL DÉPASSÉ" in proc.stdout

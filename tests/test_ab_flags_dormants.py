"""Tests du lot 2.3 — A/B des flags dormants."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.ab_flags_dormants import (
    FLAGS,
    SEUIL_TACHES,
    _analyser,
)


def _run_dict(task_id: str, success: bool, **kw) -> dict:
    base = {
        "task_id": task_id,
        "category": "expert",
        "success": success,
        "detail": "ok",
        "latency_s": 42.0,
        "tokens_generated": 100,
        "tokens_per_sec": 2.4,
        "tool_calls_total": 5,
        "tool_calls_broken": 0,
        "iterations": 4,
        "error": None,
    }
    base.update(kw)
    return base


def _ecrire_run(tmp_path: Path, name: str, resultats: list[dict]) -> Path:
    f = tmp_path / f"{name}.json"
    f.write_text(json.dumps({"meta": {}, "results": resultats}))
    return f


class TestGateDeuxTaches:
    """Le verdict ne bascule que si ≥ 2 tâches d'écart."""

    def test_on_gagne_2_taches(self, tmp_path):
        on = _ecrire_run(tmp_path, "on", [
            _run_dict("expert/a", True),
            _run_dict("expert/b", True),
            _run_dict("expert/c", True),
        ])
        off = _ecrire_run(tmp_path, "off", [
            _run_dict("expert/a", False),
            _run_dict("expert/b", False),
            _run_dict("expert/c", True),
        ])
        synth = _analyser("TEST_FLAG", [on], [off])
        assert synth["verdict"] == "ON_GAGNE"
        assert synth["exit_code"] == 2
        assert synth["ecart"] >= SEUIL_TACHES

    def test_ecart_1_pas_significatif(self, tmp_path):
        on = _ecrire_run(tmp_path, "on", [
            _run_dict("expert/a", True),
            _run_dict("expert/b", True),
        ])
        off = _ecrire_run(tmp_path, "off", [
            _run_dict("expert/a", False),
            _run_dict("expert/b", True),
        ])
        synth = _analyser("TEST_FLAG", [on], [off])
        assert synth["verdict"] == "PAS_D_ECART"
        assert synth["exit_code"] == 0

    def test_egalite_parfaite(self, tmp_path):
        on = _ecrire_run(tmp_path, "on", [
            _run_dict("expert/a", True),
            _run_dict("expert/b", False),
        ])
        off = _ecrire_run(tmp_path, "off", [
            _run_dict("expert/a", True),
            _run_dict("expert/b", False),
        ])
        synth = _analyser("TEST_FLAG", [on], [off])
        assert synth["verdict"] == "PAS_D_ECART"
        assert synth["ecart"] == 0

    def test_off_gagne(self, tmp_path):
        on = _ecrire_run(tmp_path, "on", [
            _run_dict("expert/a", False),
            _run_dict("expert/b", False),
            _run_dict("expert/c", False),
        ])
        off = _ecrire_run(tmp_path, "off", [
            _run_dict("expert/a", True),
            _run_dict("expert/b", True),
            _run_dict("expert/c", True),
        ])
        synth = _analyser("TEST_FLAG", [on], [off])
        assert synth["verdict"] == "OFF_GAGNE"
        assert synth["exit_code"] == 0


class TestSyntheseContenu:
    """La synthèse porte les bons champs."""

    def test_champs_requis(self, tmp_path):
        on = _ecrire_run(tmp_path, "on", [_run_dict("expert/a", True)])
        off = _ecrire_run(tmp_path, "off", [_run_dict("expert/a", True)])
        synth = _analyser("X", [on], [off])
        for champ in ("flag", "verdict", "exit_code", "on_gagne",
                       "off_gagne", "egalite", "ecart", "seuil", "taches"):
            assert champ in synth, f"champ manquant : {champ}"

    def test_taches_portent_les_taux(self, tmp_path):
        on = _ecrire_run(tmp_path, "on", [_run_dict("expert/a", True)])
        off = _ecrire_run(tmp_path, "off", [_run_dict("expert/a", False)])
        synth = _analyser("X", [on], [off])
        t = synth["taches"][0]
        assert t["on_rate"] == 1.0
        assert t["off_rate"] == 0.0
        assert t["delta"] == 1.0


class TestSeuilVerrouille:
    """Le seuil est un littéral, pas dérivé d'une variable."""

    def test_seuil_est_2(self):
        assert SEUIL_TACHES == 2


class TestFlags:
    """Les 3 flags sont bien déclarés."""

    def test_trois_flags(self):
        assert len(FLAGS) == 3

    def test_chaque_flag_a_env_on_off_label(self):
        for name, spec in FLAGS.items():
            assert "env_on" in spec, f"{name} sans env_on"
            assert "env_off" in spec, f"{name} sans env_off"
            assert "label" in spec, f"{name} sans label"

    def test_self_critique_valeurs(self):
        assert FLAGS["SELF_CRITIQUE_ENABLED"]["env_on"] == {"SELF_CRITIQUE_ENABLED": "true"}
        assert FLAGS["SELF_CRITIQUE_ENABLED"]["env_off"] == {"SELF_CRITIQUE_ENABLED": "false"}

    def test_preview_feedback_valeurs(self):
        assert FLAGS["PREVIEW_FEEDBACK_TIMEOUT_S"]["env_on"] == {"PREVIEW_FEEDBACK_TIMEOUT_S": "5"}
        assert FLAGS["PREVIEW_FEEDBACK_TIMEOUT_S"]["env_off"] == {"PREVIEW_FEEDBACK_TIMEOUT_S": "0"}


class TestDryRun:
    """Le dry-run ne lance rien et sort 0."""

    def test_dry_run_sort_0(self):
        from scripts.ab_flags_dormants import main
        rc = main(["--flag", "SELF_CRITIQUE_ENABLED", "--dry-run"])
        assert rc == 0


class TestCLI:
    """Le script se lance et produit les bons codes de sortie."""

    def test_sans_flag_ni_all_sort_erreur(self):
        proc = subprocess.run(
            [sys.executable, "scripts/ab_flags_dormants.py"],
            capture_output=True, text=True, timeout=10,
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert proc.returncode == 2

    def test_help(self):
        proc = subprocess.run(
            [sys.executable, "scripts/ab_flags_dormants.py", "--help"],
            capture_output=True, text=True, timeout=10,
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert proc.returncode == 0
        assert "SELF_CRITIQUE_ENABLED" in proc.stdout

    def test_analyse_existants(self, tmp_path):
        on = _ecrire_run(tmp_path, "on", [
            _run_dict("expert/a", True),
            _run_dict("expert/b", True),
        ])
        off = _ecrire_run(tmp_path, "off", [
            _run_dict("expert/a", True),
            _run_dict("expert/b", True),
        ])
        out_json = tmp_path / "verdict.json"
        proc = subprocess.run(
            [sys.executable, "scripts/ab_flags_dormants.py",
             "--flag", "SELF_CRITIQUE_ENABLED",
             "--on-results", str(on),
             "--off-results", str(off),
             "--json", str(out_json)],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert proc.returncode == 0, proc.stderr
        verdict = json.loads(out_json.read_text())
        assert isinstance(verdict, list)
        assert verdict[0]["verdict"] == "PAS_D_ECART"

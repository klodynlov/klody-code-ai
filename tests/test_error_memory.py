"""Tests pour agent.error_memory — signatures + agrégation."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.error_memory import ErrorEntry, ErrorMemory, _signature


class TestSignature:
    def test_module_not_found(self):
        stderr = "ModuleNotFoundError: No module named 'pandas'"
        assert _signature(stderr) == "ModuleNotFoundError: pandas"

    def test_module_not_found_chemin_pointe(self):
        stderr = "ImportError: No module named 'mypkg.submod'"
        assert _signature(stderr) == "ImportError: mypkg.submod"

    def test_attribute_error_nonetype(self):
        stderr = "AttributeError: 'NoneType' object has no attribute 'foo'"
        assert _signature(stderr) == "AttributeError NoneType.foo"

    def test_syntax_error(self):
        stderr = '  File "/tmp/test/app.py", line 10\n    def f(:\n          ^\nSyntaxError: invalid syntax'
        sig = _signature(stderr)
        assert sig is not None
        assert "SyntaxError" in sig
        assert "app.py" in sig

    def test_pytest_failure(self):
        stderr = "FAILED tests/test_x.py::test_foo - AssertionError"
        sig = _signature(stderr)
        assert sig is not None
        assert "AssertionError" in sig
        assert "test_foo" in sig

    def test_fallback_dernier_exception(self):
        stderr = "Traceback ...\nValueError: invalid input"
        sig = _signature(stderr)
        assert sig is not None
        assert "ValueError" in sig

    def test_stderr_vide(self):
        assert _signature("") is None
        assert _signature("   ") is None


class TestErrorMemory:
    def test_record_renvoie_signature(self, tmp_path):
        em = ErrorMemory(workdir=tmp_path)
        sig = em.record("ModuleNotFoundError: No module named 'numpy'")
        assert sig == "ModuleNotFoundError: numpy"
        assert len(em.entries) == 1

    def test_record_sans_signature_renvoie_none(self, tmp_path):
        em = ErrorMemory(workdir=tmp_path)
        sig = em.record("juste du texte sans erreur")
        assert sig is None
        assert len(em.entries) == 0

    def test_persistance_sur_disque(self, tmp_path):
        em1 = ErrorMemory(workdir=tmp_path)
        em1.record("ModuleNotFoundError: No module named 'requests'")
        em2 = ErrorMemory(workdir=tmp_path)
        assert len(em2.entries) == 1
        assert em2.entries[0].signature == "ModuleNotFoundError: requests"

    def test_recurrent_seuil(self, tmp_path):
        em = ErrorMemory(workdir=tmp_path)
        for _ in range(3):
            em.record("ModuleNotFoundError: No module named 'pandas'")
        em.record("ValueError: x")  # 1 fois seulement
        rec = em.recurrent(min_count=3)
        assert len(rec) == 1
        assert rec[0][0] == "ModuleNotFoundError: pandas"
        assert rec[0][1] == 3

    def test_format_for_prompt(self, tmp_path):
        em = ErrorMemory(workdir=tmp_path)
        for _ in range(3):
            em.record("ModuleNotFoundError: No module named 'pandas'")
        s = em.format_for_prompt(min_count=3)
        assert "pandas" in s
        assert "3×" in s
        assert "Erreurs récurrentes" in s

    def test_format_vide_si_pas_recurrent(self, tmp_path):
        em = ErrorMemory(workdir=tmp_path)
        em.record("ValueError: rare")
        assert em.format_for_prompt(min_count=3) == ""

    def test_rotation_100_entrees(self, tmp_path):
        em = ErrorMemory(workdir=tmp_path)
        for i in range(150):
            em.record(f"ValueError: bug {i}")
        # Rotation : max 100 entrées conservées
        assert len(em.entries) == 100

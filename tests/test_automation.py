"""Tests pour tools/automation.py — renommage, organisation, sauvegarde, synchro."""
from __future__ import annotations

import tarfile

import pytest
from tools.automation import (
    AutomationError,
    _resolve_dir,
    backup_directory,
    batch_rename,
    organize_directory,
    sync_directories,
)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Dossier de travail sous cwd → dans les racines autorisées."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    return d


# ── sandbox ──────────────────────────────────────────────────────────────────

class TestSandbox:
    def test_reject_outside(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(AutomationError):
            _resolve_dir("/etc")

    def test_batch_rename_outside(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert "SÉCURITÉ" in batch_rename("/etc", "a", "b")


# ── batch_rename ─────────────────────────────────────────────────────────────

class TestBatchRename:
    def test_dry_run_default(self, workdir):
        (workdir / "photo_old.jpg").write_text("x")
        result = batch_rename(str(workdir), "old", "new")
        assert "SIMULATION" in result
        assert (workdir / "photo_old.jpg").exists()      # rien renommé
        assert not (workdir / "photo_new.jpg").exists()

    def test_apply(self, workdir):
        (workdir / "photo_old.jpg").write_text("x")
        result = batch_rename(str(workdir), "old", "new", dry_run=False)
        assert "APPLIQUÉ" in result
        assert (workdir / "photo_new.jpg").exists()
        assert not (workdir / "photo_old.jpg").exists()

    def test_regex(self, workdir):
        (workdir / "IMG_0001.png").write_text("x")
        batch_rename(str(workdir), r"IMG_(\d+)", r"vacances_\1", use_regex=True, dry_run=False)
        assert (workdir / "vacances_0001.png").exists()

    def test_invalid_regex(self, workdir):
        assert "regex invalide" in batch_rename(str(workdir), "(unclosed", "x", use_regex=True)

    def test_skips_sensitive(self, workdir):
        (workdir / "secret.pem").write_text("KEY")
        result = batch_rename(str(workdir), "secret", "public", dry_run=False)
        assert (workdir / "secret.pem").exists()          # jamais renommé
        assert "sensible" in result or "Aucun" in result

    def test_no_match(self, workdir):
        (workdir / "a.txt").write_text("x")
        assert "Aucun fichier" in batch_rename(str(workdir), "zzz", "y")


# ── organize_directory ───────────────────────────────────────────────────────

class TestOrganize:
    def test_by_type_dry_run(self, workdir):
        (workdir / "a.jpg").write_text("x")
        (workdir / "b.pdf").write_text("x")
        result = organize_directory(str(workdir))
        assert "SIMULATION" in result
        assert "Images" in result and "Documents" in result
        assert (workdir / "a.jpg").exists()               # pas déplacé

    def test_by_type_apply(self, workdir):
        (workdir / "a.jpg").write_text("x")
        (workdir / "b.py").write_text("x")
        organize_directory(str(workdir), dry_run=False)
        assert (workdir / "Images" / "a.jpg").exists()
        assert (workdir / "Code" / "b.py").exists()

    def test_by_date(self, workdir):
        (workdir / "note.txt").write_text("x")
        result = organize_directory(str(workdir), by="date", dry_run=False)
        assert "APPLIQUÉ" in result
        # un sous-dossier AAAA-MM a été créé
        assert any(p.is_dir() and p.name[:4].isdigit() for p in workdir.iterdir())

    def test_invalid_by(self, workdir):
        assert "'type' ou 'date'" in organize_directory(str(workdir), by="size")

    def test_empty(self, workdir):
        assert "Aucun fichier" in organize_directory(str(workdir))


# ── backup_directory ─────────────────────────────────────────────────────────

class TestBackup:
    def test_creates_archive(self, workdir):
        (workdir / "f.txt").write_text("hello")
        result = backup_directory(str(workdir))
        assert "Sauvegarde créée" in result
        archive = next(workdir.parent.glob("data-backup-*.tar.gz"))
        with tarfile.open(archive) as tf:
            assert any("f.txt" in n for n in tf.getnames())

    def test_source_outside(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert "SÉCURITÉ" in backup_directory("/etc")


# ── sync_directories ─────────────────────────────────────────────────────────

class TestSync:
    def test_dry_run(self, workdir):
        (workdir / "x.txt").write_text("data")
        dst = workdir.parent / "dst"
        result = sync_directories(str(workdir), str(dst))
        assert "SIMULATION" in result
        assert not dst.exists() or not (dst / "x.txt").exists()

    def test_apply_copies_new(self, workdir):
        (workdir / "x.txt").write_text("data")
        dst = workdir.parent / "dst"
        sync_directories(str(workdir), str(dst), dry_run=False)
        assert (dst / "x.txt").read_text() == "data"

    def test_mirror_delete(self, workdir):
        (workdir / "keep.txt").write_text("k")
        dst = workdir.parent / "dst"
        dst.mkdir()
        (dst / "keep.txt").write_text("k")
        (dst / "stale.txt").write_text("old")
        sync_directories(str(workdir), str(dst), delete=True, dry_run=False)
        assert (dst / "keep.txt").exists()
        assert not (dst / "stale.txt").exists()            # extra supprimé

    def test_skips_sensitive(self, workdir):
        (workdir / "app.env").write_text("SECRET")
        dst = workdir.parent / "dst"
        sync_directories(str(workdir), str(dst), dry_run=False)
        assert not (dst / "app.env").exists()              # jamais copié

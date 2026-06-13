"""Tests vision B-lite — endpoint POST /api/upload + helpers de câblage WS.

Le front uploade une image (→ _uploads), puis joint le chemin dans `image_paths`
du message chat. Le backend préfixe une note (texte) → le cerveau appelle
analyser_image. AUCUN changement du format des messages : `content` reste une str.

Couvre : validation upload (ext, signature magique, cap taille, nom uuid),
filtrage des chemins (_safe_image_paths : que sous _uploads), note (_prefix_image_note),
signature (_sniff_image), source unique de la whitelist d'exts, et l'invariant
sandbox (UPLOADS_DIR lisible par analyser_image).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from api.server import _prefix_image_note, _safe_image_paths, _sniff_image

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """TestClient avec UPLOADS_DIR isolé sur tmp_path et LibraryBrain neutralisé."""
    monkeypatch.setattr("services.ensure_librarybrain", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        "services.get_librarybrain_status",
        lambda: {"running": False, "books": 0, "url": ""},
    )
    uploads = tmp_path / "_uploads"
    monkeypatch.setattr("config.UPLOADS_DIR", uploads)

    from api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c, uploads


class TestUpload:
    def test_png_accepte(self, client):
        c, uploads = client
        r = c.post("/api/upload", files={"file": ("photo.png", _PNG, "image/png")})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["size"] == len(_PNG)
        p = Path(body["path"])
        assert p.parent == uploads.resolve()
        assert p.read_bytes() == _PNG

    def test_jpeg_accepte(self, client):
        c, _ = client
        r = c.post("/api/upload", files={"file": ("x.jpg", _JPEG, "image/jpeg")})
        assert r.status_code == 200

    def test_webp_accepte(self, client):
        c, _ = client
        r = c.post("/api/upload", files={"file": ("x.webp", _WEBP, "image/webp")})
        assert r.status_code == 200

    def test_nom_uuid_ignore_le_nom_client(self, client):
        """Le nom de fichier client (avec traversée) est IGNORÉ : nom = uuid serveur."""
        c, uploads = client
        r = c.post("/api/upload", files={"file": ("../../evil.png", _PNG, "image/png")})
        assert r.status_code == 200
        body = r.json()
        assert "evil" not in body["name"]
        assert ".." not in body["name"]
        assert Path(body["path"]).parent == uploads.resolve()  # reste dans _uploads

    def test_extension_non_image_rejetee(self, client):
        """Bloque .txt même si le contenu ressemble à une image (ext d'abord)."""
        c, uploads = client
        r = c.post("/api/upload", files={"file": ("secret.txt", _PNG, "text/plain")})
        assert r.status_code == 415
        # Rien écrit
        assert not uploads.exists() or not any(uploads.iterdir())

    def test_contenu_non_image_rejete_malgre_extension(self, client):
        """SÉCURITÉ : .png mais octets non-image → 415 (on ne croit pas l'ext/MIME)."""
        c, uploads = client
        r = c.post("/api/upload", files={"file": ("fake.png", b"#!/bin/sh\nrm -rf", "image/png")})
        assert r.status_code == 415
        assert not uploads.exists() or not any(uploads.iterdir())

    def test_trop_volumineux_rejete_413(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr("config.VL_MAX_IMAGE_MB", 0.001)  # ~1 Ko
        big = _PNG + b"\x00" * 4096
        r = c.post("/api/upload", files={"file": ("big.png", big, "image/png")})
        assert r.status_code == 413


class TestSafeImagePaths:
    def test_garde_fichier_sous_uploads(self, monkeypatch, tmp_path):
        uploads = tmp_path / "_uploads"
        uploads.mkdir()
        monkeypatch.setattr("config.UPLOADS_DIR", uploads)
        f = uploads / "a.png"
        f.write_bytes(_PNG)
        assert _safe_image_paths([str(f)]) == [str(f.resolve())]

    def test_rejette_hors_uploads(self, monkeypatch, tmp_path):
        uploads = tmp_path / "_uploads"
        uploads.mkdir()
        monkeypatch.setattr("config.UPLOADS_DIR", uploads)
        outside = tmp_path / "evil.png"
        outside.write_bytes(_PNG)
        assert _safe_image_paths([str(outside)]) == []
        assert _safe_image_paths(["/etc/passwd"]) == []

    def test_rejette_inexistant(self, monkeypatch, tmp_path):
        uploads = tmp_path / "_uploads"
        uploads.mkdir()
        monkeypatch.setattr("config.UPLOADS_DIR", uploads)
        assert _safe_image_paths([str(uploads / "nope.png")]) == []

    def test_rejette_extension_non_image(self, monkeypatch, tmp_path):
        uploads = tmp_path / "_uploads"
        uploads.mkdir()
        monkeypatch.setattr("config.UPLOADS_DIR", uploads)
        f = uploads / "a.txt"
        f.write_bytes(_PNG)
        assert _safe_image_paths([str(f)]) == []

    def test_rejette_sous_dossier(self, monkeypatch, tmp_path):
        """Un fichier dans un sous-dossier de _uploads est rejeté (parent != base)."""
        uploads = tmp_path / "_uploads"
        sub = uploads / "sub"
        sub.mkdir(parents=True)
        monkeypatch.setattr("config.UPLOADS_DIR", uploads)
        f = sub / "a.png"
        f.write_bytes(_PNG)
        assert _safe_image_paths([str(f)]) == []

    def test_entrees_invalides(self, monkeypatch, tmp_path):
        monkeypatch.setattr("config.UPLOADS_DIR", tmp_path / "_uploads")
        assert _safe_image_paths(None) == []
        assert _safe_image_paths("pas une liste") == []
        assert _safe_image_paths([123, None, ""]) == []


class TestPrefixNote:
    def test_avec_texte(self):
        out = _prefix_image_note("regarde ça", ["/u/_uploads/a.png"])
        assert "analyser_image" in out
        assert "/u/_uploads/a.png" in out
        assert out.endswith("regarde ça")

    def test_sans_texte(self):
        out = _prefix_image_note("", ["/u/_uploads/a.png"])
        assert "analyser_image" in out
        assert "/u/_uploads/a.png" in out
        assert "\n\n" not in out.split("]")[-1]  # pas de texte appendé après la note

    def test_plusieurs_images(self):
        out = _prefix_image_note("x", ["/u/_uploads/a.png", "/u/_uploads/b.jpg"])
        assert "/u/_uploads/a.png" in out and "/u/_uploads/b.jpg" in out

    def test_reste_une_str(self):
        assert isinstance(_prefix_image_note("x", ["/u/_uploads/a.png"]), str)


class TestSniff:
    @pytest.mark.parametrize("data", [_PNG, _JPEG, _WEBP, b"GIF89a...", b"GIF87a...", b"BM...."])
    def test_signatures_connues(self, data):
        assert _sniff_image(data) is True

    @pytest.mark.parametrize("data", [b"", b"hello world", b"RIFF" + b"\x00" * 4 + b"AVI ", b"<html>"])
    def test_non_images(self, data):
        assert _sniff_image(data) is False


class TestCablage:
    def test_whitelist_exts_source_unique(self):
        """L'upload réutilise EXACTEMENT la whitelist de analyser_image (zéro drift)."""
        import api.server as srv
        import tools.vision as vision
        assert srv._IMAGE_EXTS is vision._IMAGE_EXTS

    def test_uploads_dir_lisible_par_analyser_image(self):
        """Invariant sandbox : un fichier dans UPLOADS_DIR passe la validation
        de analyser_image (UPLOADS_DIR est sous une racine vision autorisée)."""
        from config import PROJECT_ROOT, UPLOADS_DIR, build_allowed_roots, match_allowed_root
        roots = build_allowed_roots(PROJECT_ROOT)
        assert match_allowed_root(UPLOADS_DIR.resolve(), roots) is not None

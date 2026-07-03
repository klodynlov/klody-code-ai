"""ASI02 — garde-fou chemins des serveurs MCP (klody_mcp/_pathguard.py)."""

import pytest
from klody_mcp import _pathguard
from klody_mcp._pathguard import PathGuardViolation, safe_path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Racine autorisée = tmp_path uniquement (isole du home réel)."""
    monkeypatch.setattr(_pathguard, "AUDIO_ROOTS", [tmp_path.resolve()])
    return tmp_path


class TestLectureConfinee:
    def test_fichier_sous_racine_ok(self, roots):
        f = roots / "son.wav"
        f.write_bytes(b"RIFF")
        assert safe_path(str(f)) == f.resolve()

    def test_traversal_hors_racine_bloquee(self, roots):
        with pytest.raises(PathGuardViolation):
            safe_path("/etc/passwd")

    def test_traversal_relative_bloquee(self, roots):
        with pytest.raises(PathGuardViolation):
            safe_path(str(roots / ".." / ".." / "etc" / "passwd"))

    def test_ssh_bloque(self, roots):
        with pytest.raises(PathGuardViolation):
            safe_path("~/.ssh/id_ed25519")

    def test_absent_leve_filenotfound(self, roots):
        with pytest.raises(FileNotFoundError):
            safe_path(str(roots / "absent.wav"))

    def test_symlink_sortant_bloque(self, roots):
        """Un symlink DANS la racine visant l'extérieur est rejeté (resolve suit
        le lien, puis teste l'appartenance)."""
        link = roots / "evil.wav"
        link.symlink_to("/etc/hosts")
        with pytest.raises(PathGuardViolation):
            safe_path(str(link))


class TestEcritureConfinee:
    def test_nouveau_fichier_sous_racine_ok(self, roots):
        target = roots / "sous" / "out.wav"
        target.parent.mkdir()
        got = safe_path(str(target), for_write=True)
        assert got == target.parent.resolve() / "out.wav"

    def test_ecriture_hors_racine_bloquee(self, roots):
        with pytest.raises(PathGuardViolation):
            safe_path("/etc/evil.wav", for_write=True)


class TestModelNameAllowlist:
    """entrainer_voix / statut_entrainement — allowlist stricte du nom."""

    def test_noms_valides(self):
        from klody_mcp.vocalbrain_server import _valid_model_name
        for nom in ("ma_voix", "voix-v2", "Klody42", "a"):
            assert _valid_model_name(nom) == nom

    def test_noms_invalides(self):
        from klody_mcp.vocalbrain_server import _valid_model_name
        for nom in ("../evil", "a/b", "nom.pth", "with space", "", "x" * 65,
                    "a;rm -rf", "$(whoami)"):
            assert _valid_model_name(nom) is None

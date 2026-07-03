"""ASI03 — mode lecture seule Gmail (GMAIL_READONLY) bloque les mutations."""

from klody_mcp import gmail_server as g


def _fn(tool):
    """Fonction brute derrière un outil FastMCP."""
    return getattr(tool, "fn", tool)


class TestReadonlyBloque:
    def test_send_bloque(self, monkeypatch):
        monkeypatch.setattr(g, "GMAIL_READONLY", True)
        out = _fn(g.send_email)("x@y.com", "s", "b")
        assert "error" in out and "lecture seule" in out["error"]

    def test_draft_bloque(self, monkeypatch):
        monkeypatch.setattr(g, "GMAIL_READONLY", True)
        out = _fn(g.create_draft)("x@y.com", "s", "b")
        assert "error" in out and "lecture seule" in out["error"]

    def test_modify_labels_bloque(self, monkeypatch):
        monkeypatch.setattr(g, "GMAIL_READONLY", True)
        out = _fn(g.modify_labels)("1", "INBOX", ["X"])
        assert "error" in out and "lecture seule" in out["error"]


class TestParDefautPermissif:
    """Défaut OFF = non-cassant : la garde laisse passer (l'appel réel échoue
    ensuite sur l'absence de creds, pas sur la lecture seule)."""

    def test_send_passe_la_garde(self, monkeypatch):
        monkeypatch.setattr(g, "GMAIL_READONLY", False)
        assert g._write_blocked("send_email") is None

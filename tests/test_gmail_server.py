"""Tests pour klody_mcp/gmail_server.py — helpers purs, sans réseau."""

from email.header import Header
from email.message import EmailMessage
from unittest.mock import MagicMock


# ── _decode_header ───────────────────────────────────────────────────────────

class TestDecodeHeader:
    def test_plain(self):
        from klody_mcp.gmail_server import _decode_header
        assert _decode_header("Hello") == "Hello"

    def test_rfc2047(self):
        from klody_mcp.gmail_server import _decode_header
        encoded = Header("école élève", "utf-8").encode()
        assert _decode_header(encoded) == "école élève"

    def test_none(self):
        from klody_mcp.gmail_server import _decode_header
        assert _decode_header(None) == ""


# ── _strip_html ──────────────────────────────────────────────────────────────

class TestStripHtml:
    def test_removes_tags(self):
        from klody_mcp.gmail_server import _strip_html
        out = _strip_html("<p>Bonjour</p>")
        assert "Bonjour" in out and "<p>" not in out

    def test_unescapes_entities(self):
        from klody_mcp.gmail_server import _strip_html
        assert _strip_html("Tom &amp; Jerry").strip() == "Tom & Jerry"

    def test_drops_script_and_style(self):
        from klody_mcp.gmail_server import _strip_html
        out = _strip_html("<style>p{color:red}</style><p>Hi</p><script>evil()</script>")
        assert "Hi" in out and "evil" not in out and "color" not in out


# ── _extract_body ────────────────────────────────────────────────────────────

class TestExtractBody:
    def test_prefers_plain_over_html(self):
        from klody_mcp.gmail_server import _extract_body
        msg = EmailMessage()
        msg.set_content("Texte brut")
        msg.add_alternative("<p>Version HTML</p>", subtype="html")
        body = _extract_body(msg)
        assert "Texte brut" in body
        assert "Version HTML" not in body

    def test_html_only_fallback(self):
        from klody_mcp.gmail_server import _extract_body
        msg = EmailMessage()
        msg.set_content("<h1>Salut</h1>", subtype="html")
        assert "Salut" in _extract_body(msg)

    def test_truncates(self):
        from klody_mcp.gmail_server import _extract_body
        msg = EmailMessage()
        msg.set_content("A" * 10000)
        assert len(_extract_body(msg, max_chars=100)) == 100


# ── _build_message ───────────────────────────────────────────────────────────

class TestBuildMessage:
    def test_headers_and_body(self, monkeypatch):
        import klody_mcp.gmail_server as g
        monkeypatch.setattr(g, "GMAIL_ADDRESS", "me@gmail.com")
        msg = g._build_message("a@x.com", "Sujet", "Corps", cc="c@x.com")
        assert msg["From"] == "me@gmail.com"
        assert msg["To"] == "a@x.com"
        assert msg["Cc"] == "c@x.com"
        assert msg["Subject"] == "Sujet"
        assert "Corps" in msg.get_content()

    def test_optional_headers_absent(self, monkeypatch):
        import klody_mcp.gmail_server as g
        monkeypatch.setattr(g, "GMAIL_ADDRESS", "me@gmail.com")
        msg = g._build_message("a@x.com", "S", "B")
        assert msg["Cc"] is None
        assert msg["Bcc"] is None


# ── _parse_labels ────────────────────────────────────────────────────────────

class TestParseLabels:
    def test_system_and_user_labels(self):
        from klody_mcp.gmail_server import _parse_labels
        raw = b'1 (X-GM-LABELS (\\Inbox \\Important "Mon Label") UID 42)'
        labels = _parse_labels(raw)
        assert "\\Inbox" in labels
        assert "\\Important" in labels
        assert "Mon Label" in labels

    def test_no_labels_section(self):
        from klody_mcp.gmail_server import _parse_labels
        assert _parse_labels(b"1 (UID 42)") == []


# ── _find_special_folder ─────────────────────────────────────────────────────

class TestFindSpecialFolder:
    def _conn(self, lines):
        conn = MagicMock()
        conn.list.return_value = ("OK", lines)
        return conn

    def test_finds_drafts(self):
        from klody_mcp.gmail_server import _find_special_folder
        conn = self._conn([
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"',
            b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
        ])
        assert _find_special_folder(conn, "\\Drafts") == "[Gmail]/Drafts"

    def test_localized_folder_name(self):
        from klody_mcp.gmail_server import _find_special_folder
        conn = self._conn([b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Brouillons"'])
        assert _find_special_folder(conn, "\\Drafts") == "[Gmail]/Brouillons"

    def test_not_found(self):
        from klody_mcp.gmail_server import _find_special_folder
        conn = self._conn([b'(\\HasNoChildren) "/" "INBOX"'])
        assert _find_special_folder(conn, "\\Drafts") is None


# ── _q / _uids ───────────────────────────────────────────────────────────────

class TestQuote:
    def test_simple(self):
        from klody_mcp.gmail_server import _q
        assert _q("INBOX") == '"INBOX"'

    def test_name_with_spaces(self):
        from klody_mcp.gmail_server import _q
        assert _q("[Gmail]/All Mail") == '"[Gmail]/All Mail"'


class TestUids:
    def test_flatten(self):
        from klody_mcp.gmail_server import _uids
        assert _uids([b"1 2 3"]) == [b"1", b"2", b"3"]

    def test_empty(self):
        from klody_mcp.gmail_server import _uids
        assert _uids([b""]) == []
        assert _uids([None]) == []
        assert _uids([]) == []

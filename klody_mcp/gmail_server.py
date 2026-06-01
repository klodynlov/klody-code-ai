"""Gmail MCP server — IMAP/SMTP via app password.

Serveur MCP autonome (façon klody_server.py) exposant des outils Gmail.
N'importe quel client MCP (Klody, Claude Desktop, Cline, Zed…) peut s'y brancher.

Authentification : adresse Gmail + mot de passe d'application (App Password),
lus depuis l'environnement / .env. Aucun secret n'est jamais hardcodé ni loggé.

    GMAIL_ADDRESS=toi@gmail.com
    GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx   # 16 car., généré sur myaccount.google.com

Démarrage :
    python -m klody_mcp.gmail_server                          # stdio (défaut)
    GMAIL_MCP_TRANSPORT=http python -m klody_mcp.gmail_server  # HTTP sur :8084

Outils exposés :
- search_emails(query, mailbox, limit)        — recherche syntaxe Gmail (X-GM-RAW)
- list_recent(mailbox, limit)                 — derniers emails d'un dossier
- read_email(uid, mailbox)                    — contenu complet d'un email
- send_email(to, subject, body, cc, bcc)      — envoi via SMTP (irréversible)
- create_draft(to, subject, body, cc, bcc)    — brouillon (APPEND IMAP, pas d'envoi)
- list_labels()                               — liste des labels/dossiers
- modify_labels(uid, mailbox, add, remove)    — ajoute/retire des labels Gmail
- set_read_state(uid, read, mailbox)          — marque lu / non-lu
"""
from __future__ import annotations

import email
import html
import imaplib
import logging
import os
import re
import smtplib
import ssl
from contextlib import contextmanager, suppress
from email.header import decode_header, make_header
from email.message import EmailMessage

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logger = logging.getLogger(__name__)

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
IMAP_HOST = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com")
SMTP_HOST = os.getenv("GMAIL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("GMAIL_SMTP_PORT", "465"))
BODY_MAX_CHARS = int(os.getenv("GMAIL_BODY_MAX_CHARS", "5000"))

mcp = FastMCP("Gmail")


# ---------------------------------------------------------------------------- #
# Helpers (purs / connexion)                                                    #
# ---------------------------------------------------------------------------- #


def _require_creds() -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_ADDRESS et GMAIL_APP_PASSWORD doivent être définis dans .env "
            "(mot de passe d'application Google, pas le mot de passe du compte)."
        )


@contextmanager
def _imap():
    """Connexion IMAP SSL Gmail, login + logout garantis."""
    _require_creds()
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        conn.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        yield conn
    finally:
        with suppress(Exception):
            conn.logout()


def _q(mailbox: str) -> str:
    """Quote un nom de dossier IMAP (gère les espaces, ex: '[Gmail]/All Mail')."""
    return '"{}"'.format(mailbox.replace('"', '\\"'))


def _decode_header(value: str | None) -> str:
    """Décode un en-tête RFC 2047 (=?UTF-8?...?=) en texte lisible."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _strip_html(raw: str) -> str:
    """Conversion HTML → texte très simple (retire balises, décode entités)."""
    no_tags = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    no_tags = re.sub(r"(?s)<[^>]+>", " ", no_tags)
    return re.sub(r"[ \t]*\n[ \t]*", "\n", html.unescape(no_tags)).strip()


def _part_text(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def _extract_body(msg: email.message.Message, max_chars: int = BODY_MAX_CHARS) -> str:
    """Extrait le corps texte d'un message (préfère text/plain, sinon HTML strippé)."""
    plain = html_body = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if "attachment" in str(part.get("Content-Disposition") or "").lower():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = _part_text(part)
            elif ctype == "text/html" and html_body is None:
                html_body = _part_text(part)
    elif msg.get_content_type() == "text/html":
        html_body = _part_text(msg)
    else:
        plain = _part_text(msg)

    body = plain if plain else _strip_html(html_body or "")
    return body[:max_chars]


def _find_special_folder(conn, attr: str) -> str | None:
    """Trouve un dossier spécial Gmail par son attribut IMAP (\\Drafts, \\Sent…).

    Robuste à la localisation (ex. '[Gmail]/Brouillons' en français).
    """
    typ, data = conn.list()
    if typ != "OK":
        return None
    for raw in data or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        if attr.lower() in line.lower():
            m = re.search(r'"([^"]+)"\s*$', line)
            return m.group(1) if m else line.split()[-1]
    return None


def _parse_labels(raw: bytes | str) -> list[str]:
    """Extrait les labels Gmail d'une réponse FETCH X-GM-LABELS (...).

    Labels système renvoyés en atomes (\\Inbox, \\Sent), labels utilisateur entre
    guillemets ("Mon Label").
    """
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
    m = re.search(r"X-GM-LABELS \(([^)]*)\)", text)
    if not m:
        return []
    labels = []
    for quoted, atom in re.findall(r'"([^"]+)"|(\\?[^\s()"]+)', m.group(1)):
        token = quoted or atom
        if token:
            labels.append(token)
    return labels


def _build_message(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _uids(data) -> list[bytes]:
    """Aplati la réponse d'un SEARCH IMAP en liste d'UID."""
    if not data or not data[0]:
        return []
    first = data[0]
    return first.split() if isinstance(first, bytes) else str(first).encode().split()


def _summaries(conn, uids: list[bytes], limit: int) -> list[dict]:
    """Récupère les en-têtes (from/subject/date) des derniers `limit` UID, plus récent d'abord."""
    selected = uids[-limit:] if limit else uids
    out: list[dict] = []
    for u in reversed(selected):
        typ, data = conn.uid(
            "FETCH", u, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
        )
        if typ != "OK":
            continue
        header_bytes = b""
        for part in data:
            if isinstance(part, tuple) and len(part) > 1:
                header_bytes = part[1]
        msg = email.message_from_bytes(header_bytes)
        out.append(
            {
                "uid": u.decode() if isinstance(u, bytes) else str(u),
                "from": _decode_header(msg.get("From")),
                "subject": _decode_header(msg.get("Subject")),
                "date": _decode_header(msg.get("Date")),
            }
        )
    return out


# ---------------------------------------------------------------------------- #
# Outils MCP                                                                    #
# ---------------------------------------------------------------------------- #


@mcp.tool()
def search_emails(query: str, mailbox: str = "INBOX", limit: int = 15) -> dict:
    """Recherche des emails avec la syntaxe de recherche Gmail (X-GM-RAW).

    Args:
        query: Requête Gmail, ex: 'from:boss is:unread', 'subject:facture newer_than:7d',
            'has:attachment'. Même syntaxe que la barre de recherche Gmail.
        mailbox: Dossier où chercher (défaut 'INBOX'; '[Gmail]/All Mail' pour tout).
        limit: Nombre max de résultats (plus récents d'abord).

    Returns:
        {"count": int, "emails": [{"uid", "from", "subject", "date"}, ...]}
    """
    try:
        with _imap() as conn:
            conn.select(_q(mailbox), readonly=True)
            escaped = query.replace("\\", "\\\\").replace('"', '\\"')
            typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{escaped}"')
            if typ != "OK":
                return {"error": f"Recherche échouée: {data}"}
            uids = _uids(data)
            return {"count": len(uids), "emails": _summaries(conn, uids, limit)}
    except Exception as exc:
        logger.error("search_emails: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def list_recent(mailbox: str = "INBOX", limit: int = 15) -> dict:
    """Liste les emails les plus récents d'un dossier.

    Args:
        mailbox: Dossier à lister (défaut 'INBOX').
        limit: Nombre d'emails (plus récents d'abord).

    Returns:
        {"count": int, "emails": [{"uid", "from", "subject", "date"}, ...]}
    """
    try:
        with _imap() as conn:
            conn.select(_q(mailbox), readonly=True)
            typ, data = conn.uid("SEARCH", None, "ALL")
            if typ != "OK":
                return {"error": f"Listing échoué: {data}"}
            uids = _uids(data)
            return {"count": len(uids), "emails": _summaries(conn, uids, limit)}
    except Exception as exc:
        logger.error("list_recent: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def read_email(uid: str, mailbox: str = "INBOX") -> dict:
    """Lit le contenu complet d'un email par son UID (dans le dossier indiqué).

    L'UID provient de search_emails / list_recent sur LE MÊME dossier.

    Args:
        uid: UID de l'email (chaîne).
        mailbox: Dossier où se trouve l'email (défaut 'INBOX').

    Returns:
        {"uid", "from", "to", "cc", "subject", "date", "labels": [...], "body"} ou {"error"}
    """
    try:
        with _imap() as conn:
            conn.select(_q(mailbox), readonly=True)
            typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[] X-GM-LABELS)")
            if typ != "OK" or not data or data[0] is None:
                return {"error": f"Email UID {uid} introuvable dans {mailbox}."}
            raw_msg = b""
            meta = b""
            for part in data:
                if isinstance(part, tuple) and len(part) > 1:
                    raw_msg = part[1]
                    meta += part[0] if isinstance(part[0], bytes) else b""
                elif isinstance(part, bytes):
                    meta += part
            msg = email.message_from_bytes(raw_msg)
            return {
                "uid": str(uid),
                "from": _decode_header(msg.get("From")),
                "to": _decode_header(msg.get("To")),
                "cc": _decode_header(msg.get("Cc")),
                "subject": _decode_header(msg.get("Subject")),
                "date": _decode_header(msg.get("Date")),
                "labels": _parse_labels(meta),
                "body": _extract_body(msg),
            }
    except Exception as exc:
        logger.error("read_email: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def send_email(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    """Envoie un email via SMTP. ATTENTION : action irréversible (l'email part vraiment).

    Args:
        to: Destinataire(s), séparés par des virgules.
        subject: Objet.
        body: Corps en texte brut.
        cc: Copie (optionnel, virgules).
        bcc: Copie cachée (optionnel, virgules).

    Returns:
        {"status": "sent", "to", "subject"} ou {"error"}
    """
    try:
        _require_creds()
        msg = _build_message(to, subject, body, cc, bcc)
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info("send_email -> %s | %s", to, subject)
        return {"status": "sent", "to": to, "subject": subject}
    except Exception as exc:
        logger.error("send_email: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def create_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    """Crée un brouillon dans Gmail (aucun envoi). À valider/envoyer manuellement.

    Args:
        to: Destinataire(s), séparés par des virgules.
        subject: Objet.
        body: Corps en texte brut.
        cc: Copie (optionnel).
        bcc: Copie cachée (optionnel).

    Returns:
        {"status": "draft_created", "folder", "subject"} ou {"error"}
    """
    try:
        msg = _build_message(to, subject, body, cc, bcc)
        with _imap() as conn:
            folder = _find_special_folder(conn, "\\Drafts") or "[Gmail]/Drafts"
            typ, resp = conn.append(_q(folder), "\\Draft", None, msg.as_bytes())
            if typ != "OK":
                return {"error": f"APPEND brouillon échoué: {resp}"}
        return {"status": "draft_created", "folder": folder, "subject": subject}
    except Exception as exc:
        logger.error("create_draft: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def list_labels() -> dict:
    """Liste tous les labels / dossiers Gmail disponibles.

    Returns:
        {"labels": [str, ...]} ou {"error"}
    """
    try:
        with _imap() as conn:
            typ, data = conn.list()
            if typ != "OK":
                return {"error": f"LIST échoué: {data}"}
            labels = []
            for raw in data or []:
                line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
                m = re.search(r'"([^"]+)"\s*$', line)
                if m:
                    labels.append(m.group(1))
            return {"labels": labels}
    except Exception as exc:
        logger.error("list_labels: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def modify_labels(
    uid: str, mailbox: str = "INBOX", add: list[str] | None = None, remove: list[str] | None = None
) -> dict:
    """Ajoute et/ou retire des labels Gmail sur un email.

    Args:
        uid: UID de l'email (issu de search/list sur le même dossier).
        mailbox: Dossier où se trouve l'email (défaut 'INBOX').
        add: Labels à ajouter (ex: ['Important', '\\Starred']).
        remove: Labels à retirer.

    Returns:
        {"uid", "added", "removed"} ou {"error"}
    """
    add = add or []
    remove = remove or []
    try:
        with _imap() as conn:
            conn.select(_q(mailbox))
            for label in add:
                conn.uid("STORE", str(uid), "+X-GM-LABELS", f'"{label}"')
            for label in remove:
                conn.uid("STORE", str(uid), "-X-GM-LABELS", f'"{label}"')
        return {"uid": str(uid), "added": add, "removed": remove}
    except Exception as exc:
        logger.error("modify_labels: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
def set_read_state(uid: str, read: bool = True, mailbox: str = "INBOX") -> dict:
    """Marque un email comme lu ou non-lu.

    Args:
        uid: UID de l'email.
        read: True = marquer lu, False = marquer non-lu.
        mailbox: Dossier où se trouve l'email (défaut 'INBOX').

    Returns:
        {"uid", "read"} ou {"error"}
    """
    try:
        with _imap() as conn:
            conn.select(_q(mailbox))
            op = "+FLAGS" if read else "-FLAGS"
            conn.uid("STORE", str(uid), op, "\\Seen")
        return {"uid": str(uid), "read": read}
    except Exception as exc:
        logger.error("set_read_state: %s", exc, exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------- #
# Entrée principale                                                            #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("GMAIL_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("GMAIL_MCP_PORT", "8084"))
    host = os.getenv("GMAIL_MCP_HOST", "127.0.0.1")

    if transport == "http":
        logger.info("Gmail MCP HTTP : http://%s:%d", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("Gmail MCP stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

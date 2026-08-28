"""Robust IMAP email reader with encoding resilience and context compression."""

from __future__ import annotations

import email
import imaplib
import re
import socket
import time
from dataclasses import dataclass, field
from email.header import decode_header
from email.utils import parseaddr
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    pass


@dataclass
class EmailSummary:
    """Structured summary of a single email."""

    uid: str
    subject: str
    sender: str
    date: str
    body_preview: str  # compressed body (up to body_max_chars)
    is_unread: bool = True
    # Extracted by the LLM later:
    deadlines: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        """Format for LLM context consumption."""
        parts = [
            f"[邮件 {self.uid}]",
            f"主题: {self.subject}",
            f"发件人: {self.sender}",
            f"时间: {self.date}",
            f"内容摘要:\n{self.body_preview}",
        ]
        return "\n".join(parts)


class EmailReaderError(Exception):
    """Base error for email reader."""


class EmailReader:
    """
    Robust IMAP email reader.

    Features:
    - SSL connection with configurable timeout & retry
    - Handles GB2312, GBK, UTF-8, Base64, QP encoded headers and bodies
    - Strips HTML to plain text for body extraction
    - Compresses long bodies to body_max_chars to save LLM tokens
    """

    _ENCODINGS_TO_TRY = ("utf-8", "gbk", "gb2312", "gb18030", "latin-1", "ascii")

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: int = 20,
        max_retries: int = 3,
        body_max_chars: int = 2000,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.max_retries = max_retries
        self.body_max_chars = body_max_chars
        self._conn: imaplib.IMAP4_SSL | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4_SSL:
        """Open an authenticated IMAP connection with retry."""
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
                conn.login(self.username, self.password)
                logger.debug("Email IMAP connected (attempt {})", attempt)
                return conn
            except (socket.timeout, socket.gaierror, imaplib.IMAP4.abort,
                    imaplib.IMAP4.error, OSError) as exc:
                last_exc = exc
                logger.warning("Email IMAP connection failed (attempt {}/{}): {}", attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)  # exponential back-off: 2s, 4s
        raise EmailReaderError(f"Cannot connect to {self.host}:{self.port} after {self.max_retries} attempts: {last_exc}") from last_exc

    def _logout(self, conn: imaplib.IMAP4_SSL) -> None:
        """Safely close an IMAP connection."""
        try:
            conn.logout()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Encoding helpers
    # ------------------------------------------------------------------

    @classmethod
    def _decode_header_value(cls, raw: str | bytes | None) -> str:
        """Decode an RFC-2047 encoded header value to a plain Python string."""
        if raw is None:
            return ""
        parts = decode_header(raw)
        out = []
        for fragment, charset in parts:
            if isinstance(fragment, bytes):
                if charset:
                    try:
                        out.append(fragment.decode(charset, errors="replace"))
                        continue
                    except (LookupError, UnicodeDecodeError):
                        pass
                # Try common encodings in order
                for enc in cls._ENCODINGS_TO_TRY:
                    try:
                        out.append(fragment.decode(enc))
                        break
                    except (UnicodeDecodeError, LookupError):
                        pass
                else:
                    out.append(fragment.decode("latin-1", errors="replace"))
            else:
                out.append(str(fragment))
        return "".join(out)

    @classmethod
    def _decode_bytes(cls, raw: bytes, charset: str | None) -> str:
        """Decode raw bytes to string with multiple fallbacks."""
        if charset:
            try:
                return raw.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                pass
        for enc in cls._ENCODINGS_TO_TRY:
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
        return raw.decode("latin-1", errors="replace")

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags, collapse whitespace."""
        text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _extract_body(self, msg: email.message.Message) -> str:
        """Extract the best available plain-text body from a message."""
        plain_parts: list[str] = []
        html_parts: list[str] = []

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition", ""))
                if "attachment" in disp:
                    continue
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes):
                    continue
                charset = part.get_content_charset()
                text = self._decode_bytes(payload, charset)
                if ctype == "text/plain":
                    plain_parts.append(text)
                elif ctype == "text/html":
                    html_parts.append(text)
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = msg.get_content_charset()
                text = self._decode_bytes(payload, charset)
                if msg.get_content_type() == "text/html":
                    html_parts.append(text)
                else:
                    plain_parts.append(text)

        body = "\n\n".join(plain_parts) if plain_parts else self._strip_html("\n\n".join(html_parts))
        return body

    def _compress_body(self, body: str) -> str:
        """Truncate body to body_max_chars with an ellipsis note."""
        body = body.strip()
        if len(body) <= self.body_max_chars:
            return body
        return body[: self.body_max_chars] + f"\n…（正文已压缩，原文约 {len(body)} 字）"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_emails(
        self,
        folder: str = "INBOX",
        limit: int = 10,
        unread_only: bool = False,
    ) -> list[EmailSummary]:
        """
        Fetch the most recent emails from a folder.

        Args:
            folder:      Mailbox folder name (default "INBOX").
            limit:       Maximum number of emails to return.
            unread_only: If True, only return unseen emails.

        Returns:
            List of EmailSummary, newest first.
        """
        conn = self._connect()
        results: list[EmailSummary] = []
        try:
            conn.select(folder, readonly=True)
            criteria = "UNSEEN" if unread_only else "ALL"
            status, data = conn.search(None, criteria)
            if status != "OK" or not data or not data[0]:
                return []

            uid_list = data[0].split()
            recent_uids = uid_list[-limit:]
            recent_uids.reverse()  # newest first

            for uid in recent_uids:
                try:
                    status, raw = conn.fetch(uid, "(RFC822 FLAGS)")
                    if status != "OK" or not raw:
                        continue
                    raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else None
                    flags_str = str(raw[0]) if isinstance(raw[0], tuple) else ""
                    if raw_bytes is None:
                        continue

                    msg = email.message_from_bytes(raw_bytes)
                    subject = self._decode_header_value(msg.get("Subject", "(无主题)"))
                    from_raw = self._decode_header_value(msg.get("From", ""))
                    _, sender_addr = parseaddr(from_raw)
                    date_str = msg.get("Date", "")
                    body = self._extract_body(msg)
                    body_preview = self._compress_body(body)
                    is_unread = "\\Seen" not in flags_str

                    results.append(EmailSummary(
                        uid=uid.decode(),
                        subject=subject,
                        sender=from_raw or sender_addr,
                        date=date_str,
                        body_preview=body_preview,
                        is_unread=is_unread,
                    ))
                except Exception as exc:
                    logger.warning("Failed to parse email uid={}: {}", uid, exc)
                    continue
        finally:
            self._logout(conn)

        return results

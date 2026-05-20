"""Email tool lets the agent list and read emails via IMAP."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from copala.agent.tools.base import Tool

if TYPE_CHECKING:
    from copala.agent.memory_rag.store import MemoryRAGStore
    from copala.config.schema import MailboxConfig
    from copala.life_events.service import LifeEventManager


class EmailTool(Tool):
    """Tool that gives the agent access to IMAP email via configured credentials."""

    def __init__(
        self,
        email_config: "MailboxConfig",
        memory_store: "MemoryRAGStore | None" = None,
        life_events: "LifeEventManager | None" = None,
    ):
        self._cfg = email_config
        self._memory = memory_store
        self._life_events = life_events
        self._channel = ""
        self._chat_id = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "email"

    @property
    def description(self) -> str:
        return (
            "Read emails from the user's configured mailbox. "
            "Actions: list=recent emails, read=full compressed body by uid, unread=unseen emails only. "
            "Use this when the user asks about their emails or inbox."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "unread"],
                    "description": "list=recent emails, read=specific email body, unread=unseen emails only",
                },
                "limit": {"type": "integer", "description": "Max emails to fetch (default 5, max 20)"},
                "uid": {"type": "string", "description": "Email UID for read"},
                "folder": {"type": "string", "description": "Mailbox folder (default INBOX)"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        limit: int = 5,
        uid: str | None = None,
        folder: str = "INBOX",
        **kwargs: Any,
    ) -> str:
        from copala.utils.email_reader import EmailReader, EmailReaderError

        limit = min(max(1, limit), 20)
        reader = EmailReader(
            host=self._cfg.imap_host,
            port=self._cfg.imap_port,
            username=self._cfg.username,
            password=self._cfg.password,
            timeout=self._cfg.timeout,
            body_max_chars=self._cfg.body_max_chars,
        )

        try:
            if action == "read":
                if not uid:
                    return "Error: 'read' action requires a uid parameter."
                emails = reader.fetch_emails(folder=folder, limit=50)
                target = next((e for e in emails if e.uid == uid), None)
                if not target:
                    return f"Error: email with uid={uid} not found in {folder}."
                text = target.to_text()
                self._remember_email(text, uid)
                await self._process_life_event(text, uid)
                return text

            unread_only = action == "unread"
            emails = reader.fetch_emails(folder=folder, limit=limit, unread_only=unread_only)
            if not emails:
                label = "unread " if unread_only else ""
                return f"No {label}emails found in {folder}."

            lines = [f"Recent {'unread ' if unread_only else ''}emails in {folder}: {len(emails)}"]
            for i, email in enumerate(emails, 1):
                text = email.to_text()
                self._remember_email(text, email.uid)
                await self._process_life_event(text, email.uid)
                unread_marker = " unread" if email.is_unread else ""
                lines.append(
                    f"{i}. [{email.uid}]{unread_marker} {email.subject}\n"
                    f"   From: {email.sender}\n"
                    f"   Date: {email.date}"
                )
            lines.append("\nUse email(action='read', uid='<uid>') to read a full compressed email.")
            return "\n".join(lines)

        except EmailReaderError as exc:
            return f"Email read error: {exc}"
        except Exception as exc:
            return f"Email unexpected error: {exc}"

    def _remember_email(self, text: str, uid: str) -> None:
        if not self._memory:
            return
        try:
            from copala.agent.memory_rag.extractor import MemoryExtractor

            items = MemoryExtractor().extract(text, source="email", source_ref=f"email:{uid}")
            if items:
                self._memory.upsert_many(items)
        except Exception:
            return

    async def _process_life_event(self, text: str, uid: str) -> None:
        if not self._life_events:
            return
        try:
            await self._life_events.process_text(
                text,
                source="email",
                source_ref=f"email:{uid}",
                notify_channel=self._channel or None,
                notify_chat_id=self._chat_id or None,
            )
        except Exception:
            return

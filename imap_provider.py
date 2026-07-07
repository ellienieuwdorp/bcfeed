"""
IMAP email provider implementation.

Supports generic IMAP servers including Gmail, iCloud, Outlook, and others.
"""

from __future__ import annotations

import email
from collections.abc import Callable
from datetime import datetime
from email.header import decode_header

from email_provider import (
    EmailMessage,
    EmailProvider,
    SearchQuery,
)
from imap_client import ImapClient, ImapConfig


class ImapProvider(EmailProvider):
    """
    IMAP-based email provider.

    Supports any IMAP-compatible email server.
    """

    def __init__(self, config: ImapConfig):
        """
        Initialize IMAP provider.

        Args:
            config: IMAP server configuration
        """
        self.config = config
        self._client = ImapClient(config)

    def authenticate(self) -> None:
        """
        Connect and authenticate with the IMAP server.

        Raises:
            AuthenticationError: If connection or login fails
        """
        self._client.authenticate()

    def search(
        self,
        query: SearchQuery,
        max_results: int = 100,
        log: Callable[[str], None] | None = None,
    ) -> list[str]:
        """
        Search for messages matching the query using IMAP UID SEARCH.

        Args:
            query: Search parameters
            max_results: Maximum number of message IDs to return
            log: Optional progress callback

        Returns:
            List of IMAP UIDs (as strings)
        """
        # Build IMAP search criteria
        criteria = self._build_search_criteria(query)

        if log:
            log(f"IMAP UID search: {' '.join(criteria)}")

        message_ids = self._client.uid_search(criteria)

        # IMAP returns oldest first; reverse for newest first
        message_ids.reverse()

        if max_results:
            # Cap the UID set BEFORE any bodies are fetched (LOG-16 IMAP twin):
            # return at most cap+1 UIDs so the pipeline can detect an over-cap
            # search (MaxResultsExceeded) without downloading the overflow.
            return message_ids[: max_results + 1]
        return message_ids

    def corroborate_empty_result(self, query: SearchQuery) -> tuple[bool, str | None]:
        """Folder corroboration for a zero-result range search (LOG-23/PY-17).

        IMAP search is folder-scoped and server-dependent: a mis-selected
        folder (or a weak server SEARCH) returns nothing, and blindly
        recording those days as checked-and-empty would poison the ledger
        under the never-re-check model. Before trusting an empty result, ask
        whether the selected folder contains *any* mail from the expected
        sender at all.

        Returns ``(trusted, diagnostic)``:
        - ``trusted`` — True when the empty result is corroborated (the folder
          demonstrably receives the sender's mail), False when empty-day
          records must NOT be written.
        - ``diagnostic`` — run-summary line describing what was searched.

        Gmail deliberately has no equivalent hook: its search is
        account-global, so there is no wrong-folder failure mode (CQ-71 note
        in gmail_provider.py).
        """
        sender = query.sender
        folder = self.config.folder or "(none)"
        if not sender:
            return True, None

        try:
            sender_uids = self._client.uid_search(["FROM", f'"{sender}"'])
        except Exception as exc:
            # Fail safe: if corroboration itself fails, do not record empty days.
            return False, (
                f'Could not verify folder "{folder}" ({exc}). '
                "These dates were left unrecorded so they can be checked again."
            )

        matched = len(sender_uids)
        if matched == 0:
            return False, (
                f'Searched folder "{folder}" — found 0 Bandcamp messages (from {sender}) '
                "in it. These dates were left unrecorded so they can be checked again — "
                "is this the right folder?"
            )
        return True, (
            f'Searched folder "{folder}" — matched 0 of {matched} messages '
            f"from {sender} for these dates."
        )

    def _build_search_criteria(self, query: SearchQuery) -> list[str]:
        """
        Build IMAP SEARCH criteria from SearchQuery.

        Note: IMAP search capabilities vary by server. We use a conservative
        set of criteria that should work on most servers. String values with
        spaces must be quoted for strict servers like iCloud.
        """
        criteria = []

        # FROM filter - quote if contains spaces
        if query.sender:
            criteria.extend(["FROM", f'"{query.sender}"'])

        # SUBJECT filter (partial match) - quote if contains spaces
        if query.subject_contains:
            criteria.extend(["SUBJECT", f'"{query.subject_contains}"'])

        # Date filters
        # IMAP uses SINCE (inclusive) and BEFORE (exclusive) with DD-Mon-YYYY format
        if query.after_date:
            imap_date = self._to_imap_date(query.after_date)
            if imap_date:
                criteria.extend(["SINCE", imap_date])

        if query.before_date:
            imap_date = self._to_imap_date(query.before_date)
            if imap_date:
                criteria.extend(["BEFORE", imap_date])

        return criteria if criteria else ["ALL"]

    def _to_imap_date(self, date_str: str) -> str | None:
        """
        Convert YYYY/MM/DD or YYYY-MM-DD to DD-Mon-YYYY for IMAP.

        Args:
            date_str: Date in YYYY/MM/DD or YYYY-MM-DD format

        Returns:
            Date in DD-Mon-YYYY format (e.g., "25-Dec-2024"), or None if invalid
        """
        # Normalize separators
        date_str = date_str.replace("/", "-")

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%d-%b-%Y")  # e.g., "25-Dec-2024"
        except ValueError:
            return None

    def fetch(
        self,
        message_ids: list[str],
        batch_size: int = 20,
        log: Callable[[str], None] | None = None,
    ) -> dict[str, EmailMessage]:
        """
        Fetch full message content for given IDs.

        Args:
            message_ids: List of IMAP UIDs
            batch_size: Used for progress logging (IMAP fetches one at a time)
            log: Optional progress callback

        Returns:
            Dict mapping message ID to EmailMessage
        """
        if not message_ids:
            return {}

        results = {}
        skipped = 0
        total = len(message_ids)

        for i, msg_id in enumerate(message_ids):
            if log and i % batch_size == 0:
                end = min(i + batch_size, total)
                log(f"Downloading messages {i} to {end}")

            # One bad message is a counted skip, never a lost batch — the
            # IMAP twin of the Gmail per-message 404 handling (LOG-17/CQ-71).
            try:
                email_msg = self._fetch_single(msg_id)
            except Exception as e:
                skipped += 1
                if log:
                    log(f"Warning: skipped message {msg_id}: {e}")
                continue
            if email_msg is None:
                skipped += 1
                if log:
                    log(f"Warning: skipped message {msg_id}: no content returned.")
                continue
            results[msg_id] = email_msg

        if skipped and log:
            log(f"Skipped {skipped} message(s) that could not be downloaded.")

        return results

    def _fetch_single(self, msg_id: str) -> EmailMessage | None:
        """
        Fetch and parse a single message.

        Args:
            msg_id: IMAP UID

        Returns:
            EmailMessage if successful, None if message couldn't be parsed
        """
        # Use BODY[] instead of RFC822 for better compatibility
        # Some servers (like iCloud) don't return message content with RFC822
        # Use UID FETCH since we're working with UIDs from search
        raw_email = self._client.uid_fetch_body(msg_id)
        if not raw_email:
            return None

        msg = email.message_from_bytes(raw_email)

        # Extract HTML body
        html_content = self._extract_html(msg)

        # Bucket onto the user's LOCAL calendar date via the shared provider
        # helper (LOG-12) — the same rule the Gmail adapter applies.
        parsed_date = self.local_date_from_header(msg.get("Date", ""))

        # Extract and decode subject
        subject = self._decode_header(msg.get("Subject", ""))

        return EmailMessage(
            html=html_content,
            date=parsed_date,
            subject=subject,
        )

    def _extract_html(self, msg: email.message.Message) -> str:
        """
        Extract HTML content from an email message.

        Walks the MIME structure to find text/html parts.

        Args:
            msg: Parsed email message

        Returns:
            HTML content as string, or empty string if not found
        """
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                # Skip attachments
                if "attachment" in content_disposition:
                    continue

                if content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
        else:
            # Single-part message
            if msg.get_content_type() == "text/html":
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")

        return ""

    def _decode_header(self, header_value: str) -> str:
        """
        Decode RFC 2047 encoded header (e.g., =?UTF-8?Q?...?=).

        Args:
            header_value: Raw header value

        Returns:
            Decoded header as string
        """
        if not header_value:
            return ""

        try:
            decoded_parts = decode_header(header_value)
            result = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    result.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    result.append(part)
            return "".join(result)
        except Exception:
            return header_value

    def close(self) -> None:
        """Close the IMAP connection."""
        self._client.close()

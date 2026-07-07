"""
Abstract email provider interface.

Defines the contract that all email providers must implement,
enabling bcfeed to work with Gmail API, IMAP, or other email backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime


@dataclass
class EmailMessage:
    """Normalized email message structure returned by providers."""

    html: str  # HTML body content (decoded)
    date: str  # YYYY-MM-DD, the message's LOCAL calendar date (LOG-12)
    subject: str  # Email subject line


@dataclass
class SearchQuery:
    """Provider-agnostic search parameters for finding Bandcamp emails."""

    sender: str  # e.g., "noreply@bandcamp.com"
    subject_contains: str  # e.g., "New release from"
    after_date: str  # YYYY/MM/DD format
    before_date: str  # YYYY/MM/DD format


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class ProviderError(Exception):
    """Raised for provider-specific errors during search/fetch."""

    pass


class EmailProvider(ABC):
    """
    Base class for email providers.

    Implementations must handle:
    - Authentication with the email service
    - Searching for messages matching criteria
    - Fetching full message content
    - Cleanup on close
    """

    @staticmethod
    def local_date_from_header(date_header: str) -> str:
        """Bucket an email onto the user's LOCAL calendar date (LOG-12).

        The one shared day-bucketing rule for every provider: parse the RFC
        2822 ``Date:`` header and convert it to the machine-local timezone
        before taking the date, so a message sent ``23:30:00 -0800`` lands on
        the same day the calendar (and the exclude-today/settling-window
        logic, which use the local ``util.today()``) agree on — identically
        through Gmail and IMAP.

        Returns ``YYYY-MM-DD``, or ``""`` when the header is missing or
        unparseable (the pipeline counts such messages as one skip each,
        LOG-24).
        """
        if not date_header:
            return ""
        try:
            parsed = parsedate_to_datetime(date_header)
        except Exception:
            return ""
        if parsed is None:
            return ""
        try:
            # Aware datetimes convert to local time; a naive one (e.g. an
            # RFC 2822 "-0000" unknown-zone offset) is assumed local as-is.
            parsed = parsed.astimezone()
        except (OSError, OverflowError, ValueError):
            pass
        return parsed.strftime("%Y-%m-%d")

    @abstractmethod
    def authenticate(self) -> None:
        """
        Authenticate with the email service.

        Raises:
            AuthenticationError: If authentication fails (invalid credentials,
                                 network issues, missing config, etc.)
        """
        pass

    @abstractmethod
    def search(
        self,
        query: SearchQuery,
        max_results: int = 100,
        log: Callable[[str], None] | None = None,
    ) -> list[str]:
        """
        Search for messages matching the query.

        Args:
            query: Search parameters (sender, subject, date range)
            max_results: Maximum number of message IDs to return
            log: Optional callback for progress logging

        Returns:
            List of message IDs (provider-specific format)

        Raises:
            AuthenticationError: If not authenticated
            ProviderError: If search fails
        """
        pass

    @abstractmethod
    def fetch(
        self,
        message_ids: list[str],
        batch_size: int = 20,
        log: Callable[[str], None] | None = None,
    ) -> dict[str, EmailMessage]:
        """
        Fetch full message content for given IDs.

        Args:
            message_ids: List of message IDs from search()
            batch_size: Number of messages to fetch per batch (for progress)
            log: Optional callback for progress logging

        Returns:
            Dict mapping message ID to EmailMessage

        Raises:
            AuthenticationError: If not authenticated
            ProviderError: If fetch fails
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Clean up resources (close connections, clear state, etc.).

        Should be safe to call multiple times.
        """
        pass

    def __enter__(self):
        """Context manager support."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager cleanup."""
        self.close()
        return False

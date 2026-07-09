"""
Gmail API provider implementation.

Wraps the existing gmail.py functionality to implement the EmailProvider interface.
"""

from __future__ import annotations

from collections.abc import Callable

from email_provider import (
    AuthenticationError,
    EmailMessage,
    EmailProvider,
    ProviderError,
    SearchQuery,
)
from gmail_client import (
    GmailAuthError,
    get_messages,
    gmail_authenticate,
    search_messages,
)


class GmailProvider(EmailProvider):
    """
    Gmail API provider using OAuth2 authentication.

    This wraps the existing gmail.py functionality to provide a consistent
    interface with the IMAP provider.

    LOG-23 asymmetry (deliberate): this provider has NO
    ``corroborate_empty_result`` method. Gmail search is account-global —
    there is no folder selection that could silently scope a search to the
    wrong mailbox — so a zero-result search needs no folder corroboration
    before the pipeline records the range as checked-and-empty. The IMAP
    provider, whose search is folder-scoped, implements that hook.
    """

    def __init__(self):
        """Initialize Gmail provider (does not authenticate yet)."""
        self._service = None

    def authenticate(self) -> None:
        """
        Authenticate with Gmail using OAuth2.

        Raises:
            AuthenticationError: If credentials are missing or invalid.
        """
        try:
            self._service = gmail_authenticate()
        except GmailAuthError as e:
            raise AuthenticationError(str(e))
        except FileNotFoundError as e:
            raise AuthenticationError(
                "Can't find your Google access file. Set up your email in Settings."
            ) from e
        except Exception as e:
            raise AuthenticationError(f"Gmail authentication failed: {e}")

    def search(
        self,
        query: SearchQuery,
        max_results: int = 100,
        log: Callable[[str], None] | None = None,
    ) -> list[str]:
        """
        Search for messages matching the query using Gmail query syntax.

        Args:
            query: Search parameters
            max_results: Maximum number of message IDs to return
            log: Optional progress callback (unused for Gmail)

        Returns:
            List of Gmail message IDs
        """
        if not self._service:
            raise AuthenticationError("Not authenticated. Call authenticate() first.")

        # Build Gmail search query from SearchQuery
        gmail_query = self._build_gmail_query(query)

        try:
            # Pagination stops early once the cap is exceeded (LOG-16/PERF-7).
            messages = search_messages(self._service, gmail_query, max_results=max_results)
            ids = [m["id"] for m in messages]
            if max_results:
                # Return at most cap+1 ids: enough for the pipeline to detect
                # an over-cap search (MaxResultsExceeded) without paging or
                # downloading the rest of the result set.
                return ids[: max_results + 1]
            return ids
        except GmailAuthError as e:
            raise AuthenticationError(str(e))
        except Exception as e:
            raise ProviderError(f"Gmail search error: {e}")

    def _build_gmail_query(self, query: SearchQuery) -> str:
        """Convert SearchQuery to Gmail search syntax."""
        parts = []

        if query.sender:
            parts.append(f"from:{query.sender}")

        if query.subject_contains:
            # Quote the phrase so Gmail matches all of it against the subject;
            # unquoted, only the first word binds to subject: (LOG-14).
            parts.append(f'subject:"{query.subject_contains}"')

        if query.after_date:
            # Gmail uses YYYY/MM/DD format
            parts.append(f"after:{query.after_date}")

        if query.before_date:
            parts.append(f"before:{query.before_date}")

        return " ".join(parts)

    def fetch(
        self,
        message_ids: list[str],
        batch_size: int = 20,
        log: Callable[[str], None] | None = None,
    ) -> dict[str, EmailMessage]:
        """
        Fetch full message content for given IDs.

        Args:
            message_ids: List of Gmail message IDs
            batch_size: Number of messages to fetch per batch
            log: Optional progress callback

        Returns:
            Dict mapping message ID to EmailMessage
        """
        if not self._service:
            raise AuthenticationError("Not authenticated. Call authenticate() first.")

        if not message_ids:
            return {}

        try:
            # get_messages pairs responses to message ids via the batch
            # callback API, so its keys ARE the Gmail message ids (CQ-19).
            raw_messages = get_messages(
                self._service,
                message_ids,
                format="full",
                batch_size=batch_size,
                log=log,
            )

            # Convert to EmailMessage format. The transport hands the raw
            # Date header through; bucketing onto the user's LOCAL calendar
            # date happens here, via the shared provider helper (LOG-12) —
            # the same rule the IMAP adapter applies.
            results = {}
            for msg_id, msg_data in raw_messages.items():
                results[msg_id] = EmailMessage(
                    html=msg_data.get("html", ""),
                    date=self.local_date_from_header(msg_data.get("date") or ""),
                    subject=msg_data.get("subject", ""),
                )

            return results

        except GmailAuthError as e:
            raise AuthenticationError(str(e))
        except Exception as e:
            raise ProviderError(f"Gmail fetch error: {e}")

    def close(self) -> None:
        """Clean up Gmail service connection."""
        self._service = None

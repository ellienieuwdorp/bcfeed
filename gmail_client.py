import base64
import json
import time
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from credential_store import (
    CredentialStoreError,
    get_gmail_client_config_json,
    get_gmail_token_json,
    save_gmail_client_config_json,
    save_gmail_token_json,
)
from credential_store import (
    clear_gmail_client_config as clear_stored_gmail_client_config,
)
from credential_store import (
    clear_gmail_token as clear_stored_gmail_token,
)
from credential_store import (
    has_gmail_client_config as has_stored_gmail_client_config,
)
from paths import CREDENTIALS_PATH, DATA_DIR, GMAIL_CREDENTIALS_FILE

# The ONLY Gmail scope bcfeed ever requests (ARC-5d/SEC-3): read-only. The app
# only calls messages().list and messages().get, and the setup docs and
# privacy page promise read-only access — nothing broader is needed.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# Retired legacy on-disk token store (SEC-10/CQ-70): the pickle migration path
# is gone. This literal filename survives only so a stale file left by an old
# install can be deleted — it is never read.
_LEGACY_TOKEN_FILENAME = "token.pickle"


class GmailAuthError(Exception):
    """Raised when Gmail OAuth credentials are missing, expired, or revoked."""


def _remove_stale_legacy_token_file() -> None:
    """One-time cleanup: unlink (never read) a stale legacy token file."""
    try:
        legacy = DATA_DIR / _LEGACY_TOKEN_FILENAME
        if legacy.exists():
            legacy.unlink()
    except Exception:
        pass


def _clear_token() -> None:
    """Remove the saved authorization to force a new auth flow next run."""
    try:
        clear_stored_gmail_token()
    except Exception:
        pass


def _find_credentials_file() -> Path | None:
    """
    Look for a legacy credentials file in the app data dir or the CWD.

    bcfeed follows a user-owned-credentials model: the OAuth client secret is
    never bundled into a binary. The former branch that looked for a client
    secret inside a PyInstaller bundle root was dead and contradicted that
    model, so it was removed (WP-28 · ARCH-9). Asset bundling now goes through
    ``paths.resource_path()``; credentials never do.
    """
    candidates = [
        CREDENTIALS_PATH,
        Path.cwd() / GMAIL_CREDENTIALS_FILE,
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def gmail_credentials_configured() -> bool:
    try:
        return has_stored_gmail_client_config() or _find_credentials_file() is not None
    except CredentialStoreError:
        return _find_credentials_file() is not None


def gmail_token_available() -> bool:
    """Whether a stored, read-only-scoped Gmail authorization exists.

    Loading validates the stored scopes: a legacy full-access authorization
    (pre read-only scope) is cleared by ``_load_stored_token`` so
    ``/config.json`` reports ``has_token: false`` and the UI walks the user
    through reconnecting (ARC-5d/SEC-3).
    """
    try:
        return _load_stored_token() is not None
    except (GmailAuthError, CredentialStoreError):
        return False


def clear_gmail_credentials(*, clear_client_config: bool = True) -> None:
    """Remove stored Gmail credentials and app-managed legacy files."""
    errors: list[Exception] = []

    try:
        clear_stored_gmail_token()
    except Exception as exc:
        errors.append(exc)

    if clear_client_config:
        try:
            clear_stored_gmail_client_config()
        except Exception as exc:
            errors.append(exc)

    _remove_stale_legacy_token_file()
    try:
        if CREDENTIALS_PATH.exists():
            CREDENTIALS_PATH.unlink()
    except Exception as exc:
        errors.append(exc)

    if errors:
        # Generic message only — the underlying keychain/file detail must
        # never reach a client body (SEC-9/CQ-70); callers log the chain.
        raise GmailAuthError("Couldn't fully disconnect Gmail.") from errors[0]


def _load_stored_token() -> Credentials | None:
    token_json = get_gmail_token_json()
    if not token_json:
        return None
    try:
        payload = json.loads(token_json)
        if not isinstance(payload, dict):
            raise ValueError("Gmail token JSON must contain an object")
        creds = Credentials.from_authorized_user_info(payload)
    except Exception as exc:
        _clear_token()
        raise GmailAuthError("Gmail isn't connected. Connect it in Settings.") from exc

    # Any authorization not carrying exactly the read-only scope — which
    # includes every legacy full-mailbox token, since those never held
    # gmail.readonly — is invalidated so the reconnect flow issues a
    # read-only one (ARC-5d/SEC-3).
    if set(creds.scopes or []) != {GMAIL_SCOPE}:
        _clear_token()
        return None
    return creds


def _persist_token(creds: Credentials) -> None:
    try:
        save_gmail_token_json(creds.to_json())
    except (CredentialStoreError, ValueError) as exc:
        # Generic message only — keychain error text never reaches a client
        # body (SEC-9/CQ-70); callers log the chained detail.
        raise GmailAuthError(
            "Couldn't save the Gmail connection to your Mac's Keychain. See the server log for details."
        ) from exc


def _load_client_config() -> dict:
    raw_json = get_gmail_client_config_json()
    if raw_json:
        try:
            payload = json.loads(raw_json)
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            raise GmailAuthError("Gmail isn't connected. Connect it in Settings.") from exc

    cred_file = _find_credentials_file()
    if not cred_file:
        raise FileNotFoundError(
            "Can't find your Google access file. Set up your email in Settings."
        )

    try:
        raw_json = cred_file.read_text(encoding="utf-8")
        payload = save_gmail_client_config_json(raw_json)
    except (CredentialStoreError, ValueError) as exc:
        raise GmailAuthError(
            "Couldn't save your Google access file. See the server log for details."
        ) from exc
    except Exception as exc:
        raise GmailAuthError(
            "Couldn't read your Google access file. Try choosing it again in Settings."
        ) from exc

    if cred_file == CREDENTIALS_PATH:
        try:
            cred_file.unlink()
        except Exception:
            pass

    return payload


# ------------------------------------------------------------------------
def get_html_from_message(msg):
    """
    Extracts and decodes the HTML part from a Gmail 'full' message.
    Always returns a proper Unicode string (or None).
    """

    def walk_parts(part):
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")

        # If this part is HTML, decode it
        if mime_type == "text/html" and data:
            # Base64-url decode. The Gmail API delivers body data with its
            # Content-Transfer-Encoding already decoded, so no further
            # (quoted-printable) decoding may be applied — a speculative
            # second decode corrupts legitimate '=XX' sequences in the HTML
            # (CQ-16/PY-11).
            decoded_bytes = base64.urlsafe_b64decode(data)

            # Convert to Unicode
            return decoded_bytes.decode("utf-8", errors="replace")

        # Multipart → recursive search
        for p in part.get("parts", []):
            html = walk_parts(p)
            if html:
                return html

        return None

    return walk_parts(msg["payload"])


# ------------------------------------------------------------------------
def gmail_authenticate(oauth_timeout_seconds: float | None = None):
    """Return an authorized Gmail service, running the consent flow if needed.

    The interactive flow (``run_local_server``) blocks on a human finishing
    consent in a browser, so callers must NEVER invoke this from an HTTP
    request thread when a new authorization may be needed (SEC-6/CQ-72) —
    the server runs it on a background connect job. ``oauth_timeout_seconds``
    bounds how long the local consent server waits before giving up.
    """
    _remove_stale_legacy_token_file()
    creds = _load_stored_token()
    # if there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                _clear_token()
                raise GmailAuthError(
                    "Your Google sign-in expired. Reconnect Gmail in Settings."
                ) from exc
            except Exception as exc:
                _clear_token()
                raise GmailAuthError(
                    "Your Google sign-in expired. Reconnect Gmail in Settings."
                ) from exc
        else:
            client_config = _load_client_config()
            flow = InstalledAppFlow.from_client_config(client_config, [GMAIL_SCOPE])
            creds = flow.run_local_server(port=0, timeout_seconds=oauth_timeout_seconds)
            if creds is None:
                raise GmailAuthError("Gmail sign-in didn't finish in time. Try again.")
        _persist_token(creds)
    try:
        return build("gmail", "v1", credentials=creds)
    except HttpError as exc:
        _clear_token()
        raise GmailAuthError("Gmail access failed; please reauthorize.") from exc


# ------------------------------------------------------------------------
# Gmail's messages().list caps page size at 500 (LOG-16/PERF-7).
GMAIL_PAGE_SIZE = 500


def search_messages(service, query, max_results=None):
    """List message ids matching ``query``, stopping pagination early.

    ``max_results`` is the caller's cap: listing stops as soon as more than
    ``max_results`` ids have been collected (cap+1 is enough for the caller to
    detect an over-cap search), instead of paging the entire result set and
    discarding the tail (LOG-16/PERF-7). ``maxResults`` is passed through to
    the list call so no page fetches more ids than needed.
    """
    page_size = GMAIL_PAGE_SIZE if not max_results else min(GMAIL_PAGE_SIZE, max_results + 1)
    try:
        result = (
            service.users().messages().list(userId="me", q=query, maxResults=page_size).execute()
        )
        messages = []
        if "messages" in result:
            messages.extend(result["messages"])
        while "nextPageToken" in result:
            if max_results and len(messages) > max_results:
                break  # early stop: the caller only needs to know the cap is exceeded
            page_token = result["nextPageToken"]
            result = (
                service.users()
                .messages()
                .list(userId="me", q=query, pageToken=page_token, maxResults=page_size)
                .execute()
            )
            if "messages" in result:
                messages.extend(result["messages"])
        return messages
    except Exception as exc:
        if isinstance(exc, HttpError):
            if getattr(exc, "status_code", None) == 401 or (exc.resp and exc.resp.status == 401):
                _clear_token()
                raise GmailAuthError(
                    "Your Google sign-in was removed. Reconnect Gmail in Settings."
                ) from exc
        elif isinstance(exc, RefreshError):
            _clear_token()
            raise GmailAuthError(
                "Your Google sign-in was removed. Reconnect Gmail in Settings."
            ) from exc
        raise


# ------------------------------------------------------------------------
# Bounded exponential backoff for rate-limited batches (CQ-19/LOG-17/PY-9).
BATCH_RETRY_LIMIT = 3  # retries after the first attempt (4 attempts total)
BACKOFF_BASE_SECONDS = 1.0
_RETRYABLE_STATUSES = {429, 500, 502, 503}


def _backoff_sleep(seconds):
    """Module-level seam so tests can substitute a fake clock."""
    time.sleep(seconds)


def _http_status(exc):
    """Best-effort HTTP status from an HttpError (or lookalike)."""
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "resp", None)
        status = getattr(resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _download_batch(service, msg_ids, format, log):
    """Download one batch of messages via the batch *callback* API.

    Responses are paired to their message ids through the callback's
    ``request_id`` — never through private BatchHttpRequest internals
    (CQ-19/PY-9). Retryable statuses (429/5xx) retry the failed subset with
    bounded exponential backoff; a per-message 404 is a counted skip, not a
    run abort (LOG-17).

    Returns ``(responses, skipped)``: parsed message dicts keyed by message
    id (in request order), and the count of permanently-missing messages.
    """
    pending = list(msg_ids)
    responses = {}
    skipped = 0

    for attempt in range(BATCH_RETRY_LIMIT + 1):
        succeeded = {}
        errors = {}

        def _callback(request_id, response, exception, _ok=succeeded, _err=errors):
            if exception is not None:
                _err[request_id] = exception
            else:
                _ok[request_id] = response

        batch = service.new_batch_http_request()
        for msg_id in pending:
            batch.add(
                service.users().messages().get(userId="me", id=msg_id, format=format),
                callback=_callback,
                request_id=msg_id,
            )
        batch.execute()
        responses.update(succeeded)

        retry_ids = []
        for msg_id, exc in errors.items():
            status = _http_status(exc)
            if status == 404:
                # This message no longer exists: one counted skip, never a
                # lost batch (LOG-17 acceptance).
                skipped += 1
                if log:
                    log("Warning: skipped one message that could not be found (404).")
            elif status == 401:
                _clear_token()
                raise GmailAuthError("Gmail access revoked; please reauthorize.")
            elif status in _RETRYABLE_STATUSES:
                retry_ids.append(msg_id)
            elif isinstance(exc, Exception):
                raise exc
            else:
                raise Exception(str(exc))

        if not retry_ids:
            return responses, skipped
        if attempt == BATCH_RETRY_LIMIT:
            raise Exception(
                f"Gmail is still rate-limiting after {BATCH_RETRY_LIMIT} retries — "
                "wait a minute and try again."
            )
        delay = BACKOFF_BASE_SECONDS * (2**attempt)
        if log:
            log(
                f"Gmail rate limit hit — retrying {len(retry_ids)} message(s) in "
                f"{delay:g}s (attempt {attempt + 1} of {BATCH_RETRY_LIMIT})..."
            )
        _backoff_sleep(delay)
        pending = retry_ids

    return responses, skipped  # pragma: no cover — loop always returns/raises


def _message_entry(email_data):
    """Build the ``{html, date, subject}`` entry for one downloaded message.

    ``date`` is the RAW RFC 2822 ``Date:`` header — the transport no longer
    interprets it. Day bucketing is the provider adapter's job (WP-15 ·
    LOG-12): GmailProvider converts the header to the user's local calendar
    date through the shared ``EmailProvider.local_date_from_header`` helper,
    so both providers agree on which day a boundary-hour email belongs to.
    """
    html = get_html_from_message(email_data)

    headers = email_data.get("payload", {}).get("headers", [])
    date_header = None
    subject_header = None
    for h in headers:
        name = h.get("name", "").lower()
        if name == "date":
            date_header = h.get("value")
        if name == "subject":
            subject_header = h.get("value")

    return {"html": html, "date": date_header, "subject": subject_header}


def get_messages(service, ids, format, batch_size, log=print):
    """Batch-download messages, keyed by their Gmail message id.

    Uses the public batch callback API for response pairing, retries
    rate-limited subsets with backoff, and counts permanently-missing
    messages as skips (CQ-19/LOG-17/PY-9).
    """
    emails = {}
    skipped = 0

    for batch_start in range(0, len(ids), batch_size):
        chunk = ids[batch_start : batch_start + batch_size]
        if log:
            log(f"Downloading messages {batch_start} to {min(batch_start + len(chunk), len(ids))}")
        responses, chunk_skipped = _download_batch(service, chunk, format, log)
        skipped += chunk_skipped
        for msg_id, email_data in responses.items():
            emails[msg_id] = _message_entry(email_data)

    if skipped and log:
        log(f"Skipped {skipped} message(s) that could not be downloaded.")

    return emails

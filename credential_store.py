"""
Secure credential storage backed by the system keychain via keyring.
"""

from __future__ import annotations

import json

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

SERVICE_NAME = "bcfeed"
IMAP_PASSWORD_KEY = "imap-password"
GMAIL_CLIENT_CONFIG_KEY = "gmail-client-config"
GMAIL_TOKEN_KEY = "gmail-token"
_PROBE_KEY = "__bcfeed_keyring_probe__"


class CredentialStoreError(RuntimeError):
    """Raised when secure credential storage fails."""


class CredentialStoreUnavailableError(CredentialStoreError):
    """Raised when no usable system keychain backend is available."""


def _wrap_keyring_error(exc: Exception) -> CredentialStoreError:
    message = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, NoKeyringError):
        return CredentialStoreUnavailableError(
            "bcfeed couldn't reach your Mac's Keychain. Restart bcfeed and try again."
        )
    return CredentialStoreError(f"System keychain error: {message}")


def ensure_available() -> None:
    """Raise if the active keyring backend cannot be used."""
    try:
        keyring.get_password(SERVICE_NAME, _PROBE_KEY)
    except (KeyringError, RuntimeError) as exc:
        raise _wrap_keyring_error(exc) from exc


def is_available() -> bool:
    """Return whether the system keychain backend is available."""
    try:
        ensure_available()
    except CredentialStoreError:
        return False
    return True


def _get_secret(secret_name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE_NAME, secret_name)
    except (KeyringError, RuntimeError) as exc:
        raise _wrap_keyring_error(exc) from exc


def _set_secret(secret_name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{secret_name} cannot be empty")
    ensure_available()
    try:
        keyring.set_password(SERVICE_NAME, secret_name, value)
    except (KeyringError, RuntimeError) as exc:
        raise _wrap_keyring_error(exc) from exc


def _delete_secret(secret_name: str) -> None:
    ensure_available()
    try:
        keyring.delete_password(SERVICE_NAME, secret_name)
    except PasswordDeleteError:
        return
    except (KeyringError, RuntimeError) as exc:
        raise _wrap_keyring_error(exc) from exc


def has_imap_password() -> bool:
    return bool(_get_secret(IMAP_PASSWORD_KEY))


def get_imap_password() -> str:
    return _get_secret(IMAP_PASSWORD_KEY) or ""


def save_imap_password(password: str) -> None:
    _set_secret(IMAP_PASSWORD_KEY, password)


def clear_imap_password() -> None:
    _delete_secret(IMAP_PASSWORD_KEY)


def has_gmail_client_config() -> bool:
    return bool(_get_secret(GMAIL_CLIENT_CONFIG_KEY))


def get_gmail_client_config_json() -> str | None:
    return _get_secret(GMAIL_CLIENT_CONFIG_KEY)


def save_gmail_client_config_json(raw_json: str) -> dict:
    """Validate and store a Google OAuth client-secret JSON document.

    The payload must look like the file Google Cloud issues for an OAuth
    client — an ``installed`` (or ``web``) section carrying ``client_id`` and
    ``client_secret`` (SEC-6): arbitrary JSON is rejected before anything is
    stored. Validation errors raise ``ValueError``; callers map them to
    generic client-facing messages.
    """
    parsed = json.loads(raw_json)
    if not isinstance(parsed, dict):
        raise ValueError("Gmail credentials JSON must contain an object")
    section = parsed.get("installed") or parsed.get("web")
    if (
        not isinstance(section, dict)
        or not section.get("client_id")
        or not section.get("client_secret")
    ):
        raise ValueError(
            "Gmail credentials JSON must be an OAuth client file with an "
            "'installed' or 'web' section containing client_id and client_secret"
        )
    _set_secret(GMAIL_CLIENT_CONFIG_KEY, json.dumps(parsed))
    return parsed


def clear_gmail_client_config() -> None:
    _delete_secret(GMAIL_CLIENT_CONFIG_KEY)


def has_gmail_token() -> bool:
    return bool(_get_secret(GMAIL_TOKEN_KEY))


def get_gmail_token_json() -> str | None:
    return _get_secret(GMAIL_TOKEN_KEY)


def save_gmail_token_json(raw_json: str) -> dict:
    parsed = json.loads(raw_json)
    if not isinstance(parsed, dict):
        raise ValueError("Gmail token JSON must contain an object")
    _set_secret(GMAIL_TOKEN_KEY, json.dumps(parsed))
    return parsed


def clear_gmail_token() -> None:
    _delete_secret(GMAIL_TOKEN_KEY)

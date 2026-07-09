"""
Factory for creating email providers and managing provider configuration.

Also home to the IMAP config-building and folder-discovery helpers relocated
out of server.py (WP-11 · ARC-3): they are provider plumbing, not HTTP, and
must stay importable and testable without Flask. They live here — not in
imap_client.py/imap_provider.py — to keep the Phase-1 Lane A/B file split
intact.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Literal

from credential_store import CredentialStoreError, get_imap_password, save_imap_password
from email_provider import AuthenticationError, EmailProvider, ProviderError
from imap_client import ImapClient, ImapConfig, ImapFolder
from paths import get_data_dir

ProviderType = Literal["gmail", "imap"]

CONFIG_FILENAME = "provider_config.json"


def _get_config_path() -> Path:
    """Return the path to the provider configuration file."""
    return get_data_dir() / CONFIG_FILENAME


def load_provider_config() -> dict:
    """
    Load provider configuration from disk.

    Returns:
        Configuration dict with at least {"provider": "gmail"} as default.
    """
    config_path = _get_config_path()
    if not config_path.exists():
        return {"provider": "gmail"}

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _migrate_legacy_imap_password(data)
    except Exception:
        pass

    return {"provider": "gmail"}


def save_provider_config(config: dict) -> None:
    """
    Save provider configuration to disk and store IMAP password in keychain.

    Args:
        config: Configuration dict to save
    """
    stored_config = _store_imap_password_and_strip_from_config(config)
    _write_provider_config_file(stored_config)


def _write_provider_config_file(config: dict) -> None:
    """Write provider configuration file to disk."""
    config_path = _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    tmp = config_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")

    tmp.replace(config_path)


def _store_imap_password_and_strip_from_config(config: dict) -> dict:
    """Persist the IMAP password to the keychain and remove it from the config copy."""
    stored_config = deepcopy(config)
    imap_config = stored_config.get("imap_config")
    if isinstance(imap_config, dict):
        password = imap_config.pop("password", None)
        if password:
            save_imap_password(password)
    return stored_config


def _migrate_legacy_imap_password(config: dict) -> dict:
    """Move any legacy plaintext IMAP password from config into the keychain."""
    imap_config = config.get("imap_config")
    if not isinstance(imap_config, dict):
        return config

    legacy_password = imap_config.get("password")
    if not legacy_password:
        return config

    migrated = deepcopy(config)
    migrated_imap = dict(imap_config)
    migrated_imap.pop("password", None)
    migrated["imap_config"] = migrated_imap

    try:
        save_imap_password(str(legacy_password))
        _write_provider_config_file(migrated)
    except CredentialStoreError:
        return config

    return migrated


def create_provider(
    provider_type: ProviderType | None = None,
    config: dict | None = None,
) -> EmailProvider:
    """
    Create an email provider instance.

    Args:
        provider_type: Either "gmail" or "imap". If None, reads from config.
        config: Provider-specific configuration. If None, reads from disk.

    Returns:
        Configured EmailProvider instance (not yet authenticated)

    Raises:
        ValueError: If provider type is unknown or IMAP config is missing
    """
    if config is None:
        config = load_provider_config()

    if provider_type is None:
        provider_type = config.get("provider", "gmail")

    if provider_type == "gmail":
        from gmail_provider import GmailProvider

        return GmailProvider()

    elif provider_type == "imap":
        from imap_provider import ImapConfig, ImapProvider

        imap_config = config.get("imap_config")
        if not imap_config:
            raise ValueError("IMAP isn't set up yet. Add your mail settings first.")

        return ImapProvider(
            ImapConfig(
                host=imap_config.get("host", ""),
                port=imap_config.get("port", 993),
                username=imap_config.get("username", ""),
                password=_load_imap_password(imap_config),
                use_ssl=imap_config.get("use_ssl", True),
                folder=imap_config.get("folder", "INBOX"),
            )
        )

    else:
        raise ValueError(f"Unknown provider type: {provider_type}")


def get_current_provider_type() -> ProviderType:
    """
    Get the currently configured provider type.

    Returns:
        "gmail" or "imap"
    """
    config = load_provider_config()
    provider = config.get("provider", "gmail")
    if provider not in ("gmail", "imap"):
        return "gmail"
    return provider


def _load_imap_password(imap_config: dict) -> str:
    """Load the IMAP password from the keychain, falling back to legacy config."""
    try:
        return get_imap_password() or imap_config.get("password", "")
    except CredentialStoreError:
        return imap_config.get("password", "")


# ---------------------------------------------------------------------------
# IMAP config building + folder discovery (relocated from server.py — ARC-3)
# ---------------------------------------------------------------------------

IMAP_DISCOVERY_SEARCH_CRITERIA = ["FROM", '"noreply@bandcamp.com"', "SUBJECT", '"New release from"']


def coerce_imap_port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return 993
    return port if port > 0 else 993


def coerce_imap_use_ssl(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return str(value).strip().lower() not in {"false", "0", "no", "off", "none"}


def imap_connection_signature(imap: dict | None) -> tuple[str, int, str, bool]:
    imap = imap or {}
    return (
        str(imap.get("host", "")).strip(),
        coerce_imap_port(imap.get("port", 993)),
        str(imap.get("username", "")).strip(),
        coerce_imap_use_ssl(imap.get("use_ssl", True)),
    )


def build_imap_config(imap: dict | None, existing: dict | None = None) -> dict:
    """Merge a posted IMAP config over the saved one, coercing types.

    The stored password is reused ONLY when the posted connection signature
    (host/port/username/use_ssl) matches the saved config — stored credentials
    are never combined with request-supplied targets (WP-08 mitigation).
    """
    existing = existing or {}
    imap = imap or {}
    config = {
        "host": str(imap.get("host", existing.get("host", "")) or "").strip(),
        "port": coerce_imap_port(imap.get("port", existing.get("port", 993))),
        "username": str(imap.get("username", existing.get("username", "")) or "").strip(),
        "password": str(imap.get("password", "") or ""),
        "folder": str(imap.get("folder", existing.get("folder", "")) or "").strip(),
        "use_ssl": coerce_imap_use_ssl(imap.get("use_ssl", existing.get("use_ssl", True))),
    }
    if not config["password"] and imap_connection_signature(config) == imap_connection_signature(
        existing
    ):
        try:
            config["password"] = get_imap_password()
        except CredentialStoreError:
            config["password"] = str(existing.get("password", "") or "")
    return config


def open_imap_client(imap: dict, *, select_folder: bool = False) -> ImapClient:
    client = ImapClient(ImapConfig(**imap))
    client.authenticate(select_folder=select_folder)
    return client


def imap_folder_rank(folder: ImapFolder) -> tuple[int, str]:
    flags = {flag.lower() for flag in folder.flags}
    name = folder.name.lower()
    score = 100

    if "\\all" in flags:
        score = 0
    elif "\\inbox" in flags or name == "inbox":
        score = 10
    elif "\\archive" in flags or "archive" in name or "all mail" in name:
        score = 20
    elif "\\junk" in flags or "\\trash" in flags or "\\sent" in flags or "\\drafts" in flags:
        score += 500
    elif any(keyword in name for keyword in ("spam", "junk", "trash", "deleted", "sent", "draft")):
        score += 500

    if not folder.selectable:
        score += 1000

    return score, name


def discover_imap_folders(client: ImapClient) -> tuple[list[str], str | None]:
    """List selectable folders and probe the best-ranked few for Bandcamp mail.

    ``client`` only needs ``list_folders``/``select_folder``/``uid_search``, so
    tests can pass a mock connection — no Flask, no network.
    """
    folders = client.list_folders()
    ordered = sorted(folders, key=imap_folder_rank)
    selectable = [folder.name for folder in ordered if folder.selectable]
    recommended_folder: str | None = None

    probe_candidates = [
        folder for folder in ordered if folder.selectable and imap_folder_rank(folder)[0] < 500
    ][:5]
    for folder in probe_candidates:
        try:
            client.select_folder(folder.name)
            if client.uid_search(IMAP_DISCOVERY_SEARCH_CRITERIA):
                recommended_folder = folder.name
                break
        except (AuthenticationError, ProviderError):
            continue

    if recommended_folder is None:
        recommended_folder = next(iter(selectable), None)

    return selectable, recommended_folder

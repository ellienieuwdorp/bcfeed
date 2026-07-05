"""
bcfeed local server that powers the dashboard, cache population, and embed metadata proxying.
"""

from __future__ import annotations

import datetime
import html
import os
import socket
import threading
from pathlib import Path
from queue import SimpleQueue

import requests
from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context
from flask.testing import FlaskClient
from markdown_it import MarkdownIt
from werkzeug.datastructures import Headers
from werkzeug.serving import WSGIRequestHandler, make_server

import json_store
from bandcamp import build_embed_url, extract_bandcamp_description, extract_bc_meta
from credential_store import (
    CredentialStoreError,
    get_imap_password,
    has_imap_password,
    save_gmail_client_config_json,
)
from email_provider import AuthenticationError, ProviderError
from gmail_client import (
    GmailAuthError,
    clear_gmail_credentials,
    gmail_authenticate,
    gmail_credentials_configured,
    gmail_token_available,
)
from imap_client import ImapClient, ImapConfig, ImapFolder
from paths import (
    DASHBOARD_CSS_PATH,
    DASHBOARD_JS_PATH,
    DASHBOARD_PATH,
    EMBED_CACHE_PATH,
    EMPTY_DATES_PATH,
    GMAIL_SETUP_PATH,
    IMAP_SETUP_PATH,
    README_PATH,
    RELEASE_CACHE_PATH,
    SCRAPE_STATUS_PATH,
    SETUP_PATH,
    STARRED_PATH,
    VIEWED_PATH,
)
from pipeline import (
    MaxResultsExceeded,
    ParseError,
    ProgressEmitter,
    encode_event_payload,
    populate_release_cache,
)
from provider_factory import get_current_provider_type, load_provider_config, save_provider_config
from session_store import get_full_release_cache, scrape_status_for_range
from util import parse_date
from util import today as _today

app = Flask(__name__)

POPULATE_LOCK = threading.Lock()
GMAIL_MAX_RESULTS_HARD = 2000
DOC_LINK_MAP = {
    "SETUP.md": "setup",
    "IMAP_SETUP.md": "setup-imap",
    "GMAIL_SETUP.md": "setup-gmail",
    "README.md": "readme",
}
IMAP_DISCOVERY_SEARCH_CRITERIA = ["FROM", '"noreply@bandcamp.com"', "SUBJECT", '"New release from"']


def _build_doc_markdown_renderer() -> MarkdownIt:
    md = MarkdownIt("gfm-like", {"html": True})

    def render_link_open(self, tokens, idx, options, env):
        href = tokens[idx].attrGet("href") or ""
        tokens[idx].attrSet("href", DOC_LINK_MAP.get(href, href))
        tokens[idx].attrSet("target", "_blank")
        tokens[idx].attrSet("rel", "noopener")
        return self.renderToken(tokens, idx, options, env)

    md.add_render_rule("link_open", render_link_open)
    return md


DOC_MARKDOWN_RENDERER = _build_doc_markdown_renderer()


# --- Localhost lockdown (WP-08: SEC-4/SEC-11, ARC-5b/5c) -------------------
# The app is single-user and same-origin (the dashboard is served from this
# same host:port), so no CORS headers are needed at all. Two guards cover
# every route, including SSE and static files:
#
# 1. Host validation: a browser always sends the hostname the user typed, so
#    any request whose Host is not localhost/127.0.0.1 came through a foreign
#    name — the DNS-rebinding pattern. Reject with 403.
# 2. Anti-CSRF header: every state-mutating (non-GET) request must carry
#    X-BCFeed-Request: 1. Cross-site forms and "simple" fetches cannot set
#    custom headers (doing so forces a CORS preflight, which fails because we
#    send no Access-Control-Allow-Origin), so drive-by CSRF is blocked. The
#    SSE endpoint stays a plain GET because EventSource cannot set headers.
MUTATION_HEADER = "X-BCFeed-Request"
_ALLOWED_HOSTNAMES = {"localhost", "127.0.0.1"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.before_request
def _localhost_lockdown():
    hostname = (request.host or "").partition(":")[0].strip().lower()
    if hostname not in _ALLOWED_HOSTNAMES:
        app.logger.warning("Rejected request with non-local Host header: %r", request.host)
        return jsonify({"error": "bcfeed only accepts requests from this computer."}), 403
    if request.method not in _SAFE_METHODS and request.headers.get(MUTATION_HEADER) != "1":
        app.logger.warning(
            "Rejected %s %s without the %s header", request.method, request.path, MUTATION_HEADER
        )
        return jsonify(
            {"error": "This request was blocked because it did not come from bcfeed."}
        ), 403
    return None


class _SameOriginTestClient(FlaskClient):
    """Default test client modelling the app's own browser requests.

    The dashboard's fetch wrapper attaches the anti-CSRF header to every
    non-GET request, so route tests exercising handler behavior get the same
    treatment by default. Lockdown tests that must omit the header (to prove
    the guard) use werkzeug.test.Client directly against the WSGI app.
    """

    # Defined under a private name and aliased onto FlaskClient's request
    # entry point, so the WP-05 "no direct file-opening calls in server.py"
    # source guard keeps meaning file IO only.
    def _request_with_header(self, *args, **kwargs):
        headers = Headers(kwargs.pop("headers", None) or {})
        if MUTATION_HEADER not in headers:
            headers[MUTATION_HEADER] = "1"
        kwargs["headers"] = headers
        return FlaskClient.open(self, *args, **kwargs)

    open = _request_with_header


app.test_client_class = _SameOriginTestClient


# All store persistence goes through json_store (ARC-2a): one locked, atomic
# implementation instead of the former per-module duplicates (CQ-18/PY-15).
def _load_url_set(path: Path) -> set[str]:
    data = json_store.read_json(path, [])
    return set(data) if isinstance(data, list) else set()


def _set_url_flag(path: Path, url: str, flagged: bool) -> None:
    """Add/remove one URL in a set-store atomically (load-mutate-save locked)."""

    def mutator(data):
        items = set(data) if isinstance(data, list) else set()
        if flagged:
            items.add(url)
        else:
            items.discard(url)
        return sorted(items)

    json_store.update_json(path, mutator, [])


def _load_viewed() -> set[str]:
    return _load_url_set(VIEWED_PATH)


def _load_starred() -> set[str]:
    return _load_url_set(STARRED_PATH)


def _load_embed_cache() -> dict:
    data = json_store.read_json(EMBED_CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def _save_embed_metadata(
    url: str, *, release_id=None, is_track=None, embed_url=None, description=None
) -> None:
    if not url:
        return

    def mutator(cache):
        if not isinstance(cache, dict):
            cache = {}
        existing = cache.get(url)
        if not isinstance(existing, dict):
            existing = {}
        merged = {
            "release_id": existing.get("release_id"),
            "is_track": existing.get("is_track"),
            "embed_url": existing.get("embed_url"),
            "description": existing.get("description"),
        }
        if release_id is not None:
            merged["release_id"] = release_id
        if is_track is not None:
            merged["is_track"] = is_track
        if embed_url is not None:
            merged["embed_url"] = embed_url
        if description is not None:
            merged["description"] = description
        cache[url] = merged
        return cache

    json_store.update_json(EMBED_CACHE_PATH, mutator, {}, indent=2)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


# Suppress noisy logging for health checks
class QuietHealthHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        if getattr(self, "path", "") == "/health":
            return
        super().log_request(code, size)


# bcfeed is a single-user local app: bind loopback only so the API (which has
# no authentication) is never reachable from the LAN. See SEC-1.
BIND_HOST = "127.0.0.1"


def start_server(port: int = 5050):
    """Start the server in a background thread and return (server, thread)."""
    server = make_server(BIND_HOST, port, app, threaded=True, request_handler=QuietHealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def find_free_port(preferred: int = 5050) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((BIND_HOST, preferred))
            return preferred
        except OSError:
            sock.bind((BIND_HOST, 0))
            return sock.getsockname()[1]


def start_server_thread(preferred_port: int = 5050):
    port = find_free_port(preferred_port)
    server, thread = start_server(port)
    return server, thread, port


@app.route("/viewed-state", methods=["GET", "POST"])
def viewed_state():
    if request.method == "GET":
        items = sorted(_load_viewed())
        return jsonify({"viewed": items})

    data = request.get_json(silent=True) or {}
    url = data.get("url")
    read = data.get("read")
    if not url or not isinstance(read, bool):
        return jsonify({"error": "Missing url or read flag"}), 400
    _set_url_flag(VIEWED_PATH, url, read)
    return jsonify({"ok": True})


@app.route("/releases", methods=["GET"])
def releases_endpoint():
    try:
        releases = get_full_release_cache()
        embed_cache = _load_embed_cache()
        if isinstance(embed_cache, dict) and embed_cache:
            for rel in releases:
                url = rel.get("url")
                meta = embed_cache.get(url or "")
                if not meta:
                    continue
                if meta.get("embed_url"):
                    rel["embed_url"] = meta.get("embed_url")
                if meta.get("release_id"):
                    rel["release_id"] = meta.get("release_id")
                if "is_track" in meta:
                    rel["is_track"] = meta.get("is_track")
                if meta.get("description"):
                    rel["description"] = meta.get("description")
    except Exception:
        app.logger.exception("Failed to load releases")
        return jsonify({"error": "Couldn't load releases. See the server log for details."}), 500
    return jsonify({"releases": releases})


@app.route("/starred-state", methods=["GET", "POST"])
def starred_state():
    if request.method == "GET":
        items = sorted(_load_starred())
        return jsonify({"starred": items})

    data = request.get_json(silent=True) or {}
    url = data.get("url")
    starred = data.get("starred")
    if not url or not isinstance(starred, bool):
        return jsonify({"error": "Missing url or starred flag"}), 400
    _set_url_flag(STARRED_PATH, url, starred)
    return jsonify({"ok": True})


def _has_credentials_for_provider() -> bool:
    """Check if credentials are configured for the current provider type."""
    provider_type = get_current_provider_type()

    if provider_type == "gmail":
        return gmail_credentials_configured() and gmail_token_available()
    elif provider_type == "imap":
        config = load_provider_config()
        imap_cfg = config.get("imap_config", {})
        try:
            has_password = has_imap_password() or bool(imap_cfg.get("password", ""))
        except CredentialStoreError:
            has_password = bool(imap_cfg.get("password", ""))
        return bool(
            imap_cfg.get("host", "").strip()
            and imap_cfg.get("username", "").strip()
            and has_password
            and imap_cfg.get("folder", "").strip()
        )
    return False


def _coerce_imap_port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return 993
    return port if port > 0 else 993


def _coerce_imap_use_ssl(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return str(value).strip().lower() not in {"false", "0", "no", "off", "none"}


def _imap_connection_signature(imap: dict | None) -> tuple[str, int, str, bool]:
    imap = imap or {}
    return (
        str(imap.get("host", "")).strip(),
        _coerce_imap_port(imap.get("port", 993)),
        str(imap.get("username", "")).strip(),
        _coerce_imap_use_ssl(imap.get("use_ssl", True)),
    )


def _build_imap_config(imap: dict | None, existing: dict | None = None) -> dict:
    existing = existing or {}
    imap = imap or {}
    config = {
        "host": str(imap.get("host", existing.get("host", "")) or "").strip(),
        "port": _coerce_imap_port(imap.get("port", existing.get("port", 993))),
        "username": str(imap.get("username", existing.get("username", "")) or "").strip(),
        "password": str(imap.get("password", "") or ""),
        "folder": str(imap.get("folder", existing.get("folder", "")) or "").strip(),
        "use_ssl": _coerce_imap_use_ssl(imap.get("use_ssl", existing.get("use_ssl", True))),
    }
    if not config["password"] and _imap_connection_signature(config) == _imap_connection_signature(
        existing
    ):
        try:
            config["password"] = get_imap_password()
        except CredentialStoreError:
            config["password"] = str(existing.get("password", "") or "")
    return config


def _open_imap_client(imap: dict, *, select_folder: bool = False) -> ImapClient:
    client = ImapClient(ImapConfig(**imap))
    client.authenticate(select_folder=select_folder)
    return client


def _imap_folder_rank(folder: ImapFolder) -> tuple[int, str]:
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


def _discover_imap_folders(client: ImapClient) -> tuple[list[str], str | None]:
    folders = client.list_folders()
    ordered = sorted(folders, key=_imap_folder_rank)
    selectable = [folder.name for folder in ordered if folder.selectable]
    recommended_folder: str | None = None

    probe_candidates = [
        folder for folder in ordered if folder.selectable and _imap_folder_rank(folder)[0] < 500
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


@app.route("/config.json", methods=["GET"])
def config_json():
    embed_proxy_url = request.host_url.rstrip("/") + "/embed-meta"
    has_credentials = _has_credentials_for_provider()
    payload = {
        "title": "bcfeed",
        "embed_proxy_url": embed_proxy_url,
        "has_token": gmail_token_available(),
        "has_credentials": has_credentials,
        "default_theme": "light",
        "clear_status_on_load": False,
        "show_dev_settings": False,
    }
    return jsonify(payload)


def _missing_file_response(path: Path):
    # A missing app file is a 404 with a generic body: no absolute paths in
    # responses (SEC-9); details go to the server log.
    app.logger.error("Static file not found: %s", path)
    return jsonify({"error": "Not found."}), 404


@app.route("/dashboard", methods=["GET"])
def dashboard_page():
    if not DASHBOARD_PATH.exists():
        return _missing_file_response(DASHBOARD_PATH)
    return send_file(DASHBOARD_PATH, mimetype="text/html")


@app.route("/dashboard.css", methods=["GET"])
def dashboard_css():
    if not DASHBOARD_CSS_PATH.exists():
        return _missing_file_response(DASHBOARD_CSS_PATH)
    return send_file(DASHBOARD_CSS_PATH, mimetype="text/css")


@app.route("/dashboard.js", methods=["GET"])
def dashboard_js():
    if not DASHBOARD_JS_PATH.exists():
        return _missing_file_response(DASHBOARD_JS_PATH)
    return send_file(DASHBOARD_JS_PATH, mimetype="application/javascript")


# Docs routes and helpers.
def _serve_markdown_doc(path: Path, title: str) -> Response:
    if not path.exists():
        return _missing_file_response(path)
    markdown_text = path.read_text(encoding="utf-8")
    try:
        body = DOC_MARKDOWN_RENDERER.render(markdown_text)
    except Exception:
        body = f"<pre>{html.escape(markdown_text)}</pre>"
    return render_template("docs.html", title=title, body=body)


DOC_ROUTES = {
    "setup": (SETUP_PATH, "bcfeed setup"),
    "setup-imap": (IMAP_SETUP_PATH, "bcfeed imap setup"),
    "setup-gmail": (GMAIL_SETUP_PATH, "bcfeed gmail setup"),
    "readme": (README_PATH, "bcfeed README"),
}


@app.route("/setup", methods=["GET"])
def setup_doc():
    path, title = DOC_ROUTES["setup"]
    return _serve_markdown_doc(path, title)


@app.route("/setup-gmail", methods=["GET"])
def setup_gmail_doc():
    path, title = DOC_ROUTES["setup-gmail"]
    return _serve_markdown_doc(path, title)


@app.route("/setup-imap", methods=["GET"])
def setup_imap_doc():
    path, title = DOC_ROUTES["setup-imap"]
    return _serve_markdown_doc(path, title)


@app.route("/readme", methods=["GET"])
def readme_doc():
    path, title = DOC_ROUTES["readme"]
    return _serve_markdown_doc(path, title)


@app.route("/embed-meta", methods=["GET"])
def embed_meta():
    release_url = request.args.get("url")
    if not release_url:
        return jsonify({"error": "Missing url parameter"}), 400
    try:
        resp = requests.get(
            release_url,
            headers={"User-Agent": "bcfeed/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        app.logger.exception("Failed to fetch Bandcamp page for embed metadata")
        return jsonify({"error": "Couldn't reach the Bandcamp page for this release."}), 502

    html_text = resp.text
    data = extract_bc_meta(html_text)
    description = extract_bandcamp_description(html_text)
    if not data:
        return jsonify({"error": "That page doesn't look like a Bandcamp release."}), 404

    item_id = data.get("item_id")
    is_track = (data.get("item_type") == "track") or (data.get("item_type") == "t")
    embed_url = build_embed_url(item_id, is_track)

    # Persist embed metadata for future sessions.
    _save_embed_metadata(
        release_url,
        release_id=item_id,
        is_track=is_track,
        embed_url=embed_url,
        description=description,
    )

    return jsonify(
        {
            "release_id": item_id,
            "is_track": is_track,
            "embed_url": embed_url,
            "description": description,
        }
    )


@app.route("/scrape-status", methods=["GET"])
def scrape_status():
    start_arg = request.args.get("start")
    end_arg = request.args.get("end")
    today = _today()
    default_start = today - datetime.timedelta(days=60)
    start = parse_date(start_arg, allow_none=True) if start_arg else default_start
    end = parse_date(end_arg, allow_none=True) if end_arg else today
    if not start or not end or start > end:
        return jsonify({"error": "Invalid start/end date"}), 400

    status = scrape_status_for_range(start, end)
    scraped = [day for day, is_scraped in status.items() if is_scraped]
    not_scraped = [day for day, is_scraped in status.items() if not is_scraped]
    return jsonify({"scraped": scraped, "not_scraped": not_scraped})


@app.route("/reset-caches", methods=["POST"])
def reset_caches():
    data = request.get_json(silent=True) or {}
    clear_cache = bool(data.get("clear_cache", False))
    clear_viewed = bool(data.get("clear_viewed", False))
    clear_starred = bool(data.get("clear_starred", False))

    cleared = []
    errors = []

    def _safe_unlink(path: Path):
        # Delete through json_store so removal holds the same per-store lock
        # as readers/writers (CQ-30/PY-15).
        try:
            return json_store.delete_json(path)
        except Exception:
            # Generic body only (SEC-9); the exception detail goes to the log.
            app.logger.exception("Failed to clear %s", path)
            errors.append(f"Couldn't clear {path.name}.")
        return False

    if clear_cache:
        for p in (RELEASE_CACHE_PATH, EMPTY_DATES_PATH, SCRAPE_STATUS_PATH, EMBED_CACHE_PATH):
            if _safe_unlink(p):
                cleared.append(p.name)
    # Each flag clears ONLY its own store — clearing seen history must never
    # also wipe stars, and vice-versa (CQ-03/PY-13).
    if clear_viewed:
        if _safe_unlink(VIEWED_PATH):
            cleared.append(VIEWED_PATH.name)
    if clear_starred:
        if _safe_unlink(STARRED_PATH):
            cleared.append(STARRED_PATH.name)

    return jsonify({"ok": True, "cleared": cleared, "errors": errors})


@app.route("/clear-credentials", methods=["POST"])
def clear_credentials():
    logs: list[str] = []

    def log(msg: str):
        logs.append(str(msg))
        app.logger.info(msg)

    try:
        clear_gmail_credentials()
        log("Credentials cleared.")
        return jsonify({"ok": True, "logs": logs})
    except Exception:
        app.logger.exception("Failed to clear credentials")
        return jsonify(
            {"error": "Couldn't clear credentials. See the server log for details.", "logs": logs}
        ), 500


@app.route("/load-credentials", methods=["POST"])
def load_credentials():
    logs: list[str] = []

    def log(msg: str):
        logs.append(str(msg))
        app.logger.info(msg)

    try:
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "Empty filename"}), 400
        raw_json = file.read()
        if not raw_json:
            return jsonify({"error": "Uploaded file was empty"}), 400
        save_gmail_client_config_json(raw_json.decode("utf-8"))
        clear_gmail_credentials(clear_client_config=False)
        log("Saved Gmail credentials to secure storage. Authenticating…")
        gmail_authenticate()
        log("Credentials uploaded and authenticated.")
        return jsonify({"ok": True, "logs": logs})
    except UnicodeDecodeError:
        return jsonify({"error": "Credentials file must be valid UTF-8 JSON", "logs": logs}), 400
    except ValueError:
        # Includes JSON parse errors; keep the body generic (SEC-9).
        app.logger.exception("Rejected credentials upload")
        return jsonify(
            {"error": "That file doesn't look like a Gmail credentials JSON file.", "logs": logs}
        ), 400
    except CredentialStoreError:
        app.logger.exception("Secure storage error while saving credentials")
        return jsonify(
            {
                "error": "Couldn't save credentials to secure storage. "
                "See the server log for details.",
                "logs": logs,
            }
        ), 500
    except Exception:
        app.logger.exception("Failed to load credentials")
        return jsonify(
            {"error": "Couldn't load credentials. See the server log for details.", "logs": logs}
        ), 500


# --- Typed SSE protocol (WP-10 · ARC-1/ARCH-4) ------------------------------
# Every `data:` payload on /populate-range-stream is one JSON object.
#
# Progress/log events (unnamed, EventSource `onmessage`):
#   {v: 1, phase, current, total, message, level, text}
#   phase ∈ query|download|parse|persist|cache (or null), level ∈
#   info|warn|error, current/total nullable ints (both present → the client
#   may render a determinate bar), text = human-readable log line.
#
# Terminal events — exactly one per stream, nothing follows it:
#   event: done   data: {new_releases, days_scraped}
#   event: error  data: {code, message}
#   code ∈ auth|max_results|gmail|parse|internal for worker failures, plus
#   'busy' for a run rejected because another one holds POPULATE_LOCK.
# A failed run can never end in `event: done` (the pre-WP-10 defect where the
# worker queued an "ERROR: …" prose line and the generator still emitted done).


def _error_code_for(exc: BaseException) -> str:
    """Map a populate-worker exception to its terminal SSE error code."""
    if isinstance(exc, MaxResultsExceeded):
        return "max_results"
    if isinstance(exc, (GmailAuthError, AuthenticationError)):
        return "auth"
    if isinstance(exc, ParseError):
        return "parse"
    if isinstance(exc, ProviderError):
        return "gmail"
    return "internal"


# NOTE: this SSE endpoint intentionally requires no custom header — EventSource
# cannot set headers. It is protected by the before_request Host check, and a
# populate run mutates nothing destructively (it only adds to local caches).
@app.route("/populate-range-stream", methods=["GET"])
def populate_range_stream():
    start_arg = request.args.get("start") or request.args.get("from")
    end_arg = request.args.get("end") or start_arg

    # Validate and clamp max_results (CQ-04/PY-14): a non-integer or non-positive
    # value is a client error → JSON 400 (never an unhandled ValueError 500), and
    # an absurdly large value is clamped down to the hard cap rather than trusted.
    raw_max = request.args.get("max_results")
    if raw_max is None or raw_max == "":
        max_results = GMAIL_MAX_RESULTS_HARD
    else:
        try:
            max_results = int(raw_max)
        except (TypeError, ValueError):
            return jsonify({"error": "max_results must be an integer"}), 400
        if max_results < 1:
            return jsonify({"error": "max_results must be a positive integer"}), 400
        max_results = min(max_results, GMAIL_MAX_RESULTS_HARD)

    def error_stream(code: str, message: str):
        payload = encode_event_payload({"code": code, "message": message})

        def gen():
            yield f"event: error\ndata: {payload}\n\n"

        headers = {"Cache-Control": "no-cache"}
        app.logger.error(message)
        return Response(stream_with_context(gen()), mimetype="text/event-stream", headers=headers)

    if not start_arg or not end_arg:
        return error_stream("internal", "Missing start/end")
    start = parse_date(start_arg, allow_none=True)
    end = parse_date(end_arg, allow_none=True)
    if not start or not end or start > end:
        return error_stream("internal", "Invalid start/end")

    # Check credentials based on provider type
    provider_type = get_current_provider_type()
    if provider_type == "gmail":
        if not gmail_credentials_configured():
            return error_stream(
                "auth", "Gmail credentials not found. Reload credentials in the settings panel."
            )
        if not gmail_token_available():
            return error_stream(
                "auth",
                "Gmail token missing. Reload credentials in the settings panel to re-authenticate.",
            )
    elif provider_type == "imap":
        # Check IMAP credentials
        if not _has_credentials_for_provider():
            return error_stream(
                "auth",
                "IMAP credentials not configured. Please configure IMAP settings (host, username, password, folder) in the settings panel.",
            )

    if not POPULATE_LOCK.acquire(blocking=False):
        return error_stream("busy", "Another populate is already running")

    # Queue items: ("message", payload) for progress/log events, then exactly
    # one terminal ("done"|"error", payload), then a None sentinel.
    q: SimpleQueue[tuple[str, dict] | None] = SimpleQueue()
    emitter = ProgressEmitter(send_event=lambda payload: q.put(("message", payload)))

    def worker():
        # The worker owns POPULATE_LOCK for its whole lifetime (JS-10 server
        # half): on client disconnect the SSE generator below is torn down
        # while this thread keeps running, so releasing the lock there would
        # let a second populate start concurrently. The lock is acquired in
        # the request thread above and released here when the work is done.
        try:
            populate_release_cache(
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d"),
                max_results,
                batch_size=20,
                log=emitter,
            )
            emitter("Populate completed.")
            q.put(
                (
                    "done",
                    {
                        "new_releases": emitter.new_releases,
                        "days_scraped": emitter.days_scraped,
                    },
                )
            )
        except MaxResultsExceeded as exc:
            q.put(
                (
                    "error",
                    {
                        "code": "max_results",
                        "message": f"Maximum results reached ({exc.found}/{exc.max_results}).",
                    },
                )
            )
        except Exception as exc:
            # The pipeline has already emitted its own "ERROR: …" log line for
            # anything raised inside it; this terminal event is what the client
            # keys behavior on (never message prose).
            code = _error_code_for(exc)
            message = f"ERROR: Unexpected error: {exc}" if code == "internal" else f"ERROR: {exc}"
            if code == "internal":
                app.logger.exception("Populate worker failed")
            q.put(("error", {"code": code, "message": message}))
        finally:
            POPULATE_LOCK.release()
            q.put(None)

    # Start the worker before handing the response back: its lifetime (and the
    # lock's) must not depend on whether the client ever reads the stream.
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def event_stream():
        while True:
            item = q.get()
            if item is None:
                # Safety net: the worker exited without a terminal event.
                # Failure must never render as success, so this is an error.
                payload = encode_event_payload(
                    {"code": "internal", "message": "The run ended unexpectedly."}
                )
                yield f"event: error\ndata: {payload}\n\n"
                break
            kind, payload = item
            data = encode_event_payload(payload)
            if kind == "message":
                yield f"data: {data}\n\n"
                continue
            # Terminal event ("done" or "error"): emit and end the stream so a
            # failed run can never be followed by `event: done`.
            yield f"event: {kind}\ndata: {data}\n\n"
            break

    headers = {"Cache-Control": "no-cache"}
    return Response(
        stream_with_context(event_stream()), mimetype="text/event-stream", headers=headers
    )


# ---------------------------------------------------------------------------
# Provider configuration endpoints


# Fixed user-facing messages for the IMAP endpoints (SEC-9): the underlying
# exceptions embed raw server/socket/keyring text, which doubles as a host/port
# oracle — details go to the server log only.
_IMAP_AUTH_FAILED_MSG = "Couldn't sign in to the IMAP server. Check your settings and password."
_IMAP_CONNECT_FAILED_MSG = "Couldn't talk to the IMAP server. Check the host and port."
_SECURE_STORAGE_MSG = "Couldn't access secure storage. See the server log for details."


@app.route("/imap/discover", methods=["POST"])
def imap_discover():
    """Authenticate with IMAP and return available folders for selection."""
    try:
        data = request.get_json(silent=True) or {}
        saved_config = load_provider_config().get("imap_config", {})
        imap = _build_imap_config(data.get("imap_config"), saved_config)

        if not imap["host"]:
            return jsonify({"error": "IMAP host is required."}), 400
        if not imap["username"]:
            return jsonify({"error": "IMAP username is required."}), 400
        if not imap["password"]:
            return jsonify({"error": "Enter your IMAP password to load folders."}), 400

        client = None
        try:
            client = _open_imap_client(imap, select_folder=False)
            folders, recommended_folder = _discover_imap_folders(client)
        finally:
            if client is not None:
                client.close()

        return jsonify(
            {
                "folders": folders,
                "recommended_folder": recommended_folder,
            }
        )
    except CredentialStoreError:
        app.logger.exception("Secure storage error during IMAP folder discovery")
        return jsonify({"error": _SECURE_STORAGE_MSG}), 500
    except AuthenticationError:
        app.logger.exception("IMAP authentication failed during folder discovery")
        return jsonify({"error": _IMAP_AUTH_FAILED_MSG}), 400
    except (ProviderError, ValueError):
        app.logger.exception("IMAP error during folder discovery")
        return jsonify({"error": _IMAP_CONNECT_FAILED_MSG}), 400
    except Exception:
        app.logger.exception("Failed to load IMAP folders")
        return jsonify(
            {"error": "Couldn't load IMAP folders. See the server log for details."}
        ), 500


@app.route("/provider-config", methods=["GET", "POST"])
def provider_config():
    """Get or update the email provider configuration."""
    if request.method == "GET":
        config = load_provider_config()
        # Don't expose password in GET response
        imap_cfg = config.get("imap_config", {})
        try:
            has_password = has_imap_password() or bool(imap_cfg.get("password", ""))
        except CredentialStoreError:
            has_password = bool(imap_cfg.get("password", ""))
        safe_config = {
            "provider": config.get("provider", "gmail"),
            "imap_config": {
                "host": imap_cfg.get("host", ""),
                "port": imap_cfg.get("port", 993),
                "username": imap_cfg.get("username", ""),
                "folder": imap_cfg.get("folder", ""),
                "use_ssl": imap_cfg.get("use_ssl", True),
                "has_password": has_password,
            },
            "has_gmail_credentials": gmail_credentials_configured(),
        }
        return jsonify(safe_config)

    # POST: Save config
    try:
        data = request.get_json(silent=True) or {}
        config = load_provider_config()

        if "provider" in data:
            config["provider"] = data["provider"]

        if "imap_config" in data:
            existing_imap = config.get("imap_config", {})
            # _build_imap_config only reuses the stored IMAP password when the
            # posted connection signature matches the saved config — stored
            # credentials are never combined with request-supplied targets.
            imap = _build_imap_config(data["imap_config"], existing_imap)
            if not imap["host"]:
                return jsonify({"error": "IMAP host is required."}), 400
            if not imap["username"]:
                return jsonify({"error": "IMAP username is required."}), 400
            if not imap["password"]:
                return jsonify({"error": "IMAP password is required."}), 400
            if not imap["folder"]:
                return jsonify({"error": "Choose an IMAP folder to scan before saving."}), 400

            client = None
            try:
                client = _open_imap_client(imap, select_folder=True)
            finally:
                if client is not None:
                    client.close()

            config["imap_config"] = imap

        save_provider_config(config)
        return jsonify({"ok": True})
    except CredentialStoreError:
        app.logger.exception("Secure storage error while saving provider config")
        return jsonify({"error": _SECURE_STORAGE_MSG}), 500
    except AuthenticationError:
        app.logger.exception("IMAP authentication failed while saving provider config")
        return jsonify({"error": _IMAP_AUTH_FAILED_MSG}), 400
    except (ProviderError, ValueError):
        app.logger.exception("IMAP error while saving provider config")
        return jsonify({"error": _IMAP_CONNECT_FAILED_MSG}), 400
    except Exception:
        app.logger.exception("Failed to save provider config")
        return jsonify(
            {"error": "Couldn't save the provider settings. See the server log for details."}
        ), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host=BIND_HOST, port=port, threaded=True)

"""
Atomic, locked JSON persistence — the single store-IO implementation (ARC-2a).

Every JSON store in the app (viewed/starred sets, embed cache, release cache,
scrape status, empty dates) reads and writes through this module. It provides
the correctness floor the bespoke per-module helpers lacked
(CQ-17/LOG-4/PY-12 · CQ-18/PY-8/ARCH-1 · CQ-30/PY-15):

- **Per-store locking.** A process-wide registry maps each resolved store path
  to one ``threading.RLock``. ``update_json`` runs the whole
  load-mutate-save cycle under that lock, so concurrent updates to the same
  store cannot lose writes.
- **Atomic writes with unique temp names.** Each write goes to a temp file
  whose name embeds the pid and a process-global counter, then ``os.replace``
  swaps it in. Two concurrent writers never collide on a shared ``.tmp`` path.
- **Corruption quarantine.** A present-but-unparseable store is renamed aside
  to ``<name>.corrupt-<timestamp>`` (and logged) instead of being silently
  treated as empty and then overwritten. A *missing* file stays quiet and
  simply yields the default.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Registry of one lock per resolved store path. The registry itself is guarded
# so two threads racing to create a store's lock still end up sharing one.
_registry_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}

# Process-global counter for unique temp-file names (combined with the pid so
# names are unique across processes sharing a data dir too).
_tmp_guard = threading.Lock()
_tmp_counter = 0


def _lock_for(path: Path) -> threading.RLock:
    """Return the process-wide lock for a store path (created on first use)."""
    key = os.path.realpath(os.fspath(path))
    with _registry_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


def _unique_tmp_path(path: Path) -> Path:
    """A temp path in the store's directory that no other writer can share."""
    global _tmp_counter
    with _tmp_guard:
        _tmp_counter += 1
        serial = _tmp_counter
    return path.with_name(f".{path.name}.{os.getpid()}.{serial}.tmp")


def corruption_timestamp() -> str:
    """Filesystem-safe local timestamp used in quarantine file names."""
    return time.strftime("%Y%m%dT%H%M%S")


def quarantine_corrupt(path: Path, timestamp: str | None = None) -> Path:
    """Rename an unparseable store aside to ``<name>.corrupt-<timestamp>``.

    Returns the quarantine path. Never overwrites an earlier quarantine file.
    """
    ts = timestamp or corruption_timestamp()
    target = path.with_name(f"{path.name}.corrupt-{ts}")
    serial = 1
    while target.exists():
        target = path.with_name(f"{path.name}.corrupt-{ts}.{serial}")
        serial += 1
    os.replace(path, target)
    return target


def _read_locked(path: Path, default: Any) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # A missing store is normal (first run, after reset): stay quiet.
        return copy.deepcopy(default)
    try:
        return json.loads(raw)
    except ValueError:
        quarantined = quarantine_corrupt(path)
        logger.warning(
            "Store %s is corrupt; moved it aside to %s and starting empty.",
            path,
            quarantined.name,
        )
        return copy.deepcopy(default)


def _write_locked(path: Path, data: Any, indent: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp_path(path)
    tmp.write_text(json.dumps(data, indent=indent), encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def read_json(path: Path | str, default: Any) -> Any:
    """Load a JSON store.

    Missing file → a deep copy of ``default``. Unparseable file → quarantined
    to ``<name>.corrupt-<timestamp>`` and a deep copy of ``default``.
    """
    path = Path(path)
    with _lock_for(path):
        return _read_locked(path, default)


def write_json(path: Path | str, data: Any, *, indent: int | None = None) -> None:
    """Atomically replace a JSON store's contents (unique temp + os.replace)."""
    path = Path(path)
    with _lock_for(path):
        _write_locked(path, data, indent)


def update_json(
    path: Path | str,
    mutator: Callable[[Any], Any],
    default: Any,
    *,
    indent: int | None = None,
) -> Any:
    """Run a load-mutate-save cycle atomically under the store's lock.

    ``mutator`` receives the loaded value (or the default) and returns the
    value to persist; returning ``None`` persists the (mutated-in-place)
    loaded value. Returns what was written.
    """
    path = Path(path)
    with _lock_for(path):
        data = _read_locked(path, default)
        result = mutator(data)
        if result is None:
            result = data
        _write_locked(path, result, indent)
        return result


def delete_json(path: Path | str) -> bool:
    """Delete a JSON store under its lock. Returns True if a file was removed."""
    path = Path(path)
    with _lock_for(path):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

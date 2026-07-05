"""WP-02 seed test: importing modules must not create the real data dir.

Guards the BCFEED_DATA_DIR test seam and the removal of paths.py's
import-time mkdir side effect.
"""

import importlib
import os
import sys


def test_import_paths_no_side_effect(tmp_path, monkeypatch):
    target = tmp_path / "isolated"
    monkeypatch.setenv("BCFEED_DATA_DIR", str(target))
    # Fresh import so module-level code runs under the env override.
    for mod in ("paths", "session_store"):
        sys.modules.pop(mod, None)
    paths = importlib.import_module("paths")

    # DATA_DIR points at the override and importing created nothing on disk.
    assert paths.DATA_DIR == target
    assert not target.exists(), "import must not create the data directory"

    # get_data_dir() creates it lazily on demand.
    created = paths.get_data_dir()
    assert created == target
    assert target.is_dir()


def test_data_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BCFEED_DATA_DIR", str(tmp_path / "d"))
    sys.modules.pop("paths", None)
    paths = importlib.import_module("paths")
    assert str(tmp_path) in str(paths.RELEASE_CACHE_PATH)


def teardown_module(_module):
    # Leave a clean module table so other test files import with real config.
    os.environ.pop("BCFEED_DATA_DIR", None)
    for mod in ("paths", "session_store"):
        sys.modules.pop(mod, None)

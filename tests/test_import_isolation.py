"""WP-02 seed test: importing modules must not create the real data dir.

Guards the BCFEED_DATA_DIR test seam and the removal of paths.py's
import-time mkdir side effect.

Uses ``importlib.reload`` (never ``sys.modules.pop``) to re-run module-level
init under the env override: reload updates the module object in place, so any
other already-imported module that holds ``import paths`` / ``import
session_store`` keeps a valid reference. Popping would create a second module
object and strand those references at a stale data dir — a latent
whole-suite fragility this test must not introduce.
"""

import importlib

import paths
import session_store


def test_import_paths_no_side_effect(tmp_path, monkeypatch):
    target = tmp_path / "isolated"
    monkeypatch.setenv("BCFEED_DATA_DIR", str(target))
    # Re-run module-level code in place under the env override.
    importlib.reload(paths)

    # DATA_DIR points at the override and reloading created nothing on disk.
    assert paths.DATA_DIR == target
    assert not target.exists(), "import must not create the data directory"

    # get_data_dir() creates it lazily on demand.
    created = paths.get_data_dir()
    assert created == target
    assert target.is_dir()


def test_data_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BCFEED_DATA_DIR", str(tmp_path / "d"))
    importlib.reload(paths)
    assert str(tmp_path) in str(paths.RELEASE_CACHE_PATH)


def teardown_module(_module):
    # Restore modules to a consistent state bound to whatever BCFEED_DATA_DIR is
    # now active, reloading in place so no stranded module objects remain. The
    # conftest autouse fixture re-points the env + reloads before each test, so
    # this only needs to keep the objects self-consistent.
    importlib.reload(paths)
    importlib.reload(session_store)

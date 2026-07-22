from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

from .support import FixtureClient, RunningApp, context_slots
from .synthetic_package import legacy_package_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_COMMIT = "2d40bd5"


def _materialize_legacy(tmp_path: Path) -> Path:
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{LEGACY_COMMIT}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip(f"legacy commit {LEGACY_COMMIT} is unavailable")
    archive = tmp_path / "legacy.tar"
    subprocess.run(
        ["git", "archive", "--format=tar", f"--output={archive}", LEGACY_COMMIT],
        cwd=REPO_ROOT,
        check=True,
    )
    root = tmp_path / "legacy-source"
    root.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(root, filter="data")
    return root


def test_legacy_unproved_context_is_invalidated_but_history_survives(
    tmp_path: Path, fixture_server: str, fixture: FixtureClient
) -> None:
    legacy_source = _materialize_legacy(tmp_path)
    data_dir = tmp_path / "migrated-data"
    old = RunningApp(
        data_dir=data_dir,
        fixture_url=fixture_server,
        source_root=legacy_source,
    ).start()
    try:
        assert old.api is not None
        imported = old.api.import_package(legacy_package_bytes())
        assert imported.status == 200, imported.body.decode("utf-8", "replace")
        fixture.configure("display", asset_count=2)
        session = old.api.create_session("Legacy history")
        turn = old.api.ask(
            session["session_id"],
            "Покажи legacy лазурные активы",
            context_version=session["context_version"],
        )
        assert turn["outcome"] == "success_with_rows"
        legacy_session_id = session["session_id"]
    finally:
        old.stop()

    current = RunningApp(data_dir=data_dir, fixture_url=fixture_server).start()
    try:
        assert current.api is not None
        migrated = current.api.session(legacy_session_id)
        assert migrated["messages"]
        assert context_slots(migrated) == []
        assert migrated["context"]["pending_clarification"] is None
    finally:
        current.stop()

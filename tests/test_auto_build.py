"""Tests for auto-build server loop."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from synix_agent_mesh.config import (
    AgentMeshConfig,
    AutoBuildConfig,
    MeshConfig,
    SourceConfig,
)
from synix_agent_mesh.server import run_auto_builder


@pytest.fixture
def config_with_sources(tmp_path):
    """Config with a source directory containing some files."""
    src_dir = tmp_path / "sessions"
    src_dir.mkdir()
    (src_dir / "s1.jsonl").write_text("{}")
    (src_dir / "s2.jsonl").write_text("{}")

    return AgentMeshConfig(
        project_dir=tmp_path,
        mesh=MeshConfig(name="test"),
        sources=[
            SourceConfig(name="sessions", dir=str(src_dir), patterns=["*.jsonl"]),
        ],
        auto_build=AutoBuildConfig(enabled=True, cooldown=0, scan_interval=0),
    )


@pytest.mark.asyncio
async def test_auto_build_disabled(tmp_path):
    """Auto-builder exits immediately when disabled."""
    config = AgentMeshConfig(
        project_dir=tmp_path,
        auto_build=AutoBuildConfig(enabled=False),
    )
    # Should return without blocking
    await asyncio.wait_for(run_auto_builder(config), timeout=2)


@pytest.mark.asyncio
async def test_auto_build_triggers_on_new_files(config_with_sources):
    """Auto-builder detects new files and triggers a build."""
    config = config_with_sources
    src_dir = config.sources[0].resolved_dir
    build_count = 0

    original_run_auto_builder = run_auto_builder

    # We'll patch _run_build inside the function and use a side effect
    # to add a file after first scan, then cancel after build runs
    scan_count = 0

    async def patched_auto_builder(cfg):
        nonlocal scan_count, build_count

        # Monkey-patch asyncio.sleep to speed up and inject file creation
        original_sleep = asyncio.sleep

        async def fast_sleep(duration):
            nonlocal scan_count
            scan_count += 1
            if scan_count == 2:
                # Add a new file to trigger build detection
                (src_dir / "s3.jsonl").write_text("{}")
            if scan_count > 5:
                raise asyncio.CancelledError
            await original_sleep(0)

        with patch("asyncio.sleep", side_effect=fast_sleep):
            mock_result = MagicMock()
            mock_result.built = 1
            mock_result.cached = 2

            mock_project = MagicMock()
            mock_project.build.return_value = mock_result

            with (
                patch("synix.open_project", return_value=mock_project),
                patch(
                    "synix_agent_mesh.pipeline.build_pipeline",
                    return_value=MagicMock(),
                ),
            ):
                try:
                    await original_run_auto_builder(cfg)
                except asyncio.CancelledError:
                    pass

                build_count = mock_project.build.call_count

    await patched_auto_builder(config)
    assert build_count >= 1, "Build should have been triggered"


@pytest.mark.asyncio
async def test_auto_build_no_change_no_build(config_with_sources):
    """Auto-builder does not build when file count is unchanged."""
    config = config_with_sources
    scan_count = 0

    original_sleep = asyncio.sleep

    async def fast_sleep(duration):
        nonlocal scan_count
        scan_count += 1
        if scan_count > 3:
            raise asyncio.CancelledError
        await original_sleep(0)

    with (
        patch("asyncio.sleep", side_effect=fast_sleep),
        patch("synix.open_project") as mock_open,
    ):
        try:
            await run_auto_builder(config)
        except asyncio.CancelledError:
            pass

        mock_open.assert_not_called()

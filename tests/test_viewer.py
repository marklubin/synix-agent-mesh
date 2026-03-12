"""Tests for viewer resolution and sam view CLI command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from synix_agent_mesh.cli import cli
from synix_agent_mesh.server import _resolve_viewer_release


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# _resolve_viewer_release
# ---------------------------------------------------------------------------


def test_resolve_prefers_local():
    """_resolve_viewer_release returns 'local' when it exists."""
    project = MagicMock()
    project.releases.return_value = ["staging", "local", "prod"]
    local_release = MagicMock()
    project.release.return_value = local_release

    result = _resolve_viewer_release(project)

    project.release.assert_called_once_with("local")
    assert result is local_release


def test_resolve_falls_back_to_first():
    """_resolve_viewer_release falls back to first release when 'local' is absent."""
    project = MagicMock()
    project.releases.return_value = ["staging", "prod"]
    first_release = MagicMock()
    project.release.return_value = first_release

    result = _resolve_viewer_release(project)

    project.release.assert_called_once_with("staging")
    assert result is first_release


def test_resolve_returns_none_when_empty():
    """_resolve_viewer_release returns None when no releases exist."""
    project = MagicMock()
    project.releases.return_value = []

    result = _resolve_viewer_release(project)

    assert result is None
    project.release.assert_not_called()


# ---------------------------------------------------------------------------
# sam view CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    """Create a minimal project directory with config."""
    config = tmp_path / "agent-mesh.toml"
    config.write_text("""
[mesh]
name = "test-mesh"

[viewer]
port = 9999
host = "127.0.0.1"

[sources.data]
dir = "./data"
description = "Test data"
""")
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_view_no_project(runner, tmp_path, monkeypatch):
    """sam view fails gracefully without project config."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["view"])
    assert result.exit_code != 0
    assert "Error" in result.output


def test_view_missing_release(runner, project_dir):
    """sam view fails gracefully when the release doesn't exist."""
    with patch("synix.open_project") as mock_open:
        mock_project = MagicMock()
        mock_open.return_value = mock_project
        mock_project.release.side_effect = Exception("Release 'local' not found")

        result = runner.invoke(cli, ["view"])
        assert result.exit_code != 0
        assert "Error" in result.output
        assert "sam build" in result.output


def test_view_import_error(runner, project_dir):
    """sam view shows clear error when synix[viewer] is missing."""
    with patch("synix.open_project") as mock_open:
        mock_project = MagicMock()
        mock_open.return_value = mock_project
        mock_project.release.return_value = MagicMock()

        # Simulate missing synix.viewer by poisoning sys.modules
        with patch.dict("sys.modules", {"synix.viewer": None}):
            result = runner.invoke(cli, ["view"])
            assert result.exit_code != 0
            assert "synix[viewer]" in result.output


def test_view_browser_url_uses_localhost(runner, project_dir):
    """sam view opens browser at 127.0.0.1, not 0.0.0.0."""
    # Rewrite config with 0.0.0.0 bind address
    (project_dir / "agent-mesh.toml").write_text("""
[mesh]
name = "test-mesh"

[viewer]
port = 9999
host = "0.0.0.0"

[sources.data]
dir = "./data"
""")

    mock_viewer = MagicMock()
    mock_viewer.serve = MagicMock(side_effect=KeyboardInterrupt)

    with (
        patch("synix.open_project") as mock_open,
        patch.dict("sys.modules", {"synix.viewer": mock_viewer}),
    ):
        mock_project = MagicMock()
        mock_open.return_value = mock_project
        mock_project.release.return_value = MagicMock()

        result = runner.invoke(cli, ["view"], catch_exceptions=False)

    assert "http://127.0.0.1:9999" in result.output
    assert "http://0.0.0.0" not in result.output

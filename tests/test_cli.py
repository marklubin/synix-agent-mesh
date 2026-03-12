"""Tests for CLI commands."""

import os

import pytest
from click.testing import CliRunner

from synix_agent_mesh.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal project directory with config."""
    config = tmp_path / "agent-mesh.toml"
    config.write_text("""
[mesh]
name = "test-mesh"

[sources.data]
dir = "./data"
description = "Test data"
""")
    (tmp_path / "data").mkdir()
    return tmp_path


def test_cli_help(runner):
    """CLI shows help without errors."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "synix-agent-mesh" in result.output


def test_cli_version(runner):
    """CLI shows version."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.20.2" in result.output


def test_sources_list(runner, project_dir):
    """sources list shows configured sources."""
    result = runner.invoke(cli, ["sources", "list"], catch_exceptions=False)
    # Will fail because we're not in the project dir, but shouldn't crash
    # Testing with chdir
    old_cwd = os.getcwd()
    try:
        os.chdir(project_dir)
        result = runner.invoke(cli, ["sources", "list"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "data" in result.output
        assert "Test data" in result.output
    finally:
        os.chdir(old_cwd)


def test_sources_add(runner, project_dir):
    """sources add appends to config file."""
    old_cwd = os.getcwd()
    try:
        os.chdir(project_dir)
        result = runner.invoke(
            cli,
            ["sources", "add", "notes", "~/notes", "--patterns", "**/*.md", "--description", "My notes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Added source" in result.output

        # Verify config was updated
        content = (project_dir / "agent-mesh.toml").read_text()
        assert "[sources.notes]" in content
        assert "~/notes" in content
    finally:
        os.chdir(old_cwd)


def test_sources_disable(runner, project_dir):
    """sources disable sets enabled = false."""
    old_cwd = os.getcwd()
    try:
        os.chdir(project_dir)
        result = runner.invoke(cli, ["sources", "disable", "data"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Disabled" in result.output

        content = (project_dir / "agent-mesh.toml").read_text()
        assert "enabled = false" in content
    finally:
        os.chdir(old_cwd)


def test_search_no_project(runner, tmp_path):
    """search fails gracefully without project."""
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["search", "test query"])
        assert result.exit_code != 0
        assert "Error" in result.output
    finally:
        os.chdir(old_cwd)


def test_mcp_config(runner, project_dir):
    """mcp-config prints valid JSON."""
    old_cwd = os.getcwd()
    try:
        os.chdir(project_dir)
        result = runner.invoke(cli, ["mcp-config"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "mcpServers" in result.output
        assert "synix.mcp" in result.output
    finally:
        os.chdir(old_cwd)


def test_init_creates_project(runner, tmp_path):
    """init creates project structure."""
    target = tmp_path / "new-project"
    result = runner.invoke(cli, ["init", "--dir", str(target), "--name", "init-test"])
    assert result.exit_code == 0
    assert (target / "agent-mesh.toml").exists()
    assert (target / "sources").is_dir()

    # Verify mesh name in config
    content = (target / "agent-mesh.toml").read_text()
    assert 'name = "init-test"' in content

    # Clean up mesh
    from synix.mesh.config import resolve_mesh_root
    mesh_dir = resolve_mesh_root() / "init-test"
    if mesh_dir.exists():
        import shutil
        shutil.rmtree(mesh_dir)

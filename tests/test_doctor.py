"""Tests for health check engine."""

import json

import pytest

from synix_agent_mesh.config import AgentMeshConfig, MeshConfig, SourceConfig
from synix_agent_mesh.doctor import (
    check_build,
    check_project,
    check_search,
    check_sources,
)


@pytest.fixture
def basic_config(tmp_path):
    """Minimal AgentMeshConfig for testing."""
    return AgentMeshConfig(
        project_dir=tmp_path,
        mesh=MeshConfig(name="test-mesh"),
    )


@pytest.fixture
def config_with_sources(tmp_path):
    """Config with a mix of valid and missing sources."""
    good_dir = tmp_path / "good-source"
    good_dir.mkdir()
    (good_dir / "file1.jsonl").write_text("{}")
    (good_dir / "file2.jsonl").write_text("{}")

    return AgentMeshConfig(
        project_dir=tmp_path,
        mesh=MeshConfig(name="test-mesh"),
        sources=[
            SourceConfig(name="good", dir=str(good_dir), patterns=["*.jsonl"]),
            SourceConfig(name="missing", dir=str(tmp_path / "nope")),
            SourceConfig(name="disabled", dir="/fake", enabled=False),
        ],
    )


def test_check_project_missing_config(basic_config):
    """Flags missing agent-mesh.toml."""
    result = check_project(basic_config)
    config_check = [c for c in result.checks if c.name == "config"][0]
    assert config_check.status == "fail"


def test_check_project_has_config(basic_config):
    """Passes when agent-mesh.toml exists."""
    (basic_config.project_dir / "agent-mesh.toml").write_text("[mesh]\nname = 'test'\n")
    result = check_project(basic_config)
    config_check = [c for c in result.checks if c.name == "config"][0]
    assert config_check.status == "pass"


def test_check_project_synix_dir(basic_config):
    """Flags missing .synix directory."""
    result = check_project(basic_config)
    synix_check = [c for c in result.checks if c.name == "synix"][0]
    assert synix_check.status == "fail"


def test_check_project_synix_exists(basic_config):
    """Passes when .synix exists."""
    (basic_config.project_dir / ".synix").mkdir()
    result = check_project(basic_config)
    synix_check = [c for c in result.checks if c.name == "synix"][0]
    assert synix_check.status == "pass"


def test_check_sources_good_and_missing(config_with_sources):
    """Reports good sources and flags missing ones."""
    result = check_sources(config_with_sources)

    good = [c for c in result.checks if c.name == "good"][0]
    assert good.status == "pass"

    missing = [c for c in result.checks if c.name == "missing"][0]
    assert missing.status == "warn"
    assert "missing" in missing.message

    disabled = [c for c in result.checks if c.name == "disabled"][0]
    assert disabled.status == "pass"
    assert "disabled" in disabled.message


def test_check_sources_none(basic_config):
    """Warns when no sources configured."""
    result = check_sources(basic_config)
    assert result.status == "warn"


def test_check_build_no_synix(basic_config):
    """Fails when no .synix directory."""
    result = check_build(basic_config)
    assert result.status == "fail"


def test_check_build_no_releases(basic_config):
    """Warns when .synix exists but no releases."""
    (basic_config.project_dir / ".synix").mkdir()
    result = check_build(basic_config)
    assert result.status == "warn"


def test_check_build_with_release(basic_config):
    """Passes when local release has manifest."""
    releases_dir = basic_config.project_dir / ".synix" / "releases" / "local"
    releases_dir.mkdir(parents=True)
    manifest = {"artifacts": {"a": {}, "b": {}, "c": {}}, "built_at": "2026-03-11"}
    (releases_dir / "manifest.json").write_text(json.dumps(manifest))

    result = check_build(basic_config)
    release_check = [c for c in result.checks if c.name == "release"][0]
    assert release_check.status == "pass"
    assert "3 artifacts" in release_check.message


def test_check_search_no_index(basic_config):
    """Warns when no search index found."""
    result = check_search(basic_config)
    assert result.status == "warn"


def test_check_search_with_index(basic_config):
    """Passes when search.db exists."""
    releases_dir = basic_config.project_dir / ".synix" / "releases" / "local"
    releases_dir.mkdir(parents=True)
    db = releases_dir / "search.db"
    db.write_bytes(b"\x00" * 1024)

    result = check_search(basic_config)
    index_check = [c for c in result.checks if c.name == "index"][0]
    assert index_check.status == "pass"
    assert "1 KB" in index_check.message


def test_category_result_status(config_with_sources):
    """CategoryResult.status reflects worst check."""
    result = check_sources(config_with_sources)
    # Has both pass and warn, so overall should be warn
    assert result.status == "warn"

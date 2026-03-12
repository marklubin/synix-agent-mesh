"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest

from synix_agent_mesh.config import (
    LLMConfig,
    SourceConfig,
    _parse_config,
    load_config,
)


def test_parse_minimal_config(tmp_path):
    """Minimal config with just a source."""
    raw = {
        "mesh": {"name": "test"},
        "sources": {
            "notes": {"dir": str(tmp_path)},
        },
    }
    config = _parse_config(raw, tmp_path)

    assert config.mesh.name == "test"
    assert config.mesh.port == 7433  # default
    assert len(config.sources) == 1
    assert config.sources[0].name == "notes"
    assert config.sources[0].enabled is True


def test_parse_full_config(tmp_path):
    """Full config with all sections."""
    raw = {
        "mesh": {"name": "full-mesh", "port": 8000},
        "viewer": {"port": 9000, "host": "0.0.0.0"},
        "sources": {
            "sessions": {
                "dir": "~/.claude/projects",
                "patterns": ["**/*.jsonl"],
                "description": "Claude sessions",
            },
            "notes": {
                "dir": "~/notes",
                "patterns": ["**/*.md"],
                "enabled": False,
            },
        },
        "llm": {
            "provider": "anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-3-haiku",
            "temperature": 0.5,
            "max_tokens": 4096,
        },
        "pipeline": {
            "weekly_rollup": False,
            "work_status": False,
            "context_budget": 5000,
        },
        "deploy": {
            "server_commands": ["echo done"],
            "client_commands": [],
        },
    }
    config = _parse_config(raw, tmp_path)

    assert config.mesh.port == 8000
    assert config.viewer.port == 9000
    assert config.viewer.host == "0.0.0.0"

    assert len(config.sources) == 2
    sessions = [s for s in config.sources if s.name == "sessions"][0]
    assert sessions.enabled is True
    assert sessions.patterns == ["**/*.jsonl"]

    notes = [s for s in config.sources if s.name == "notes"][0]
    assert notes.enabled is False

    assert config.llm.provider == "anthropic"
    assert config.llm.temperature == 0.5
    assert config.pipeline.weekly_rollup is False
    assert config.pipeline.context_budget == 5000
    assert config.deploy.server_commands == ["echo done"]


def test_parse_empty_config(tmp_path):
    """Empty config should use all defaults."""
    config = _parse_config({}, tmp_path)

    assert config.mesh.name == "agent-memory"
    assert config.mesh.port == 7433
    assert config.viewer.port == 9471
    assert len(config.sources) == 0
    assert config.llm.provider == "openai-compatible"
    assert config.pipeline.weekly_rollup is True


def test_source_resolved_dir():
    """SourceConfig.resolved_dir expands ~ and resolves."""
    src = SourceConfig(name="test", dir="~/notes")
    assert src.resolved_dir == Path.home() / "notes"


def test_llm_config_to_dict():
    """LLMConfig.to_dict returns serializable dict."""
    llm = LLMConfig(provider="test", base_url="http://localhost", model="m1")
    d = llm.to_dict()
    assert d["provider"] == "test"
    assert d["model"] == "m1"
    assert "temperature" in d


def test_load_config_missing_file(tmp_path):
    """load_config raises FileNotFoundError when no config file."""
    with pytest.raises(FileNotFoundError, match="agent-mesh.toml"):
        load_config(tmp_path)


def test_load_config_from_file(tmp_path):
    """load_config reads from agent-mesh.toml."""
    config_path = tmp_path / "agent-mesh.toml"
    config_path.write_text("""
[mesh]
name = "file-test"
port = 9999

[sources.data]
dir = "./data"
""")
    config = load_config(tmp_path)
    assert config.mesh.name == "file-test"
    assert config.mesh.port == 9999
    assert config.sources[0].name == "data"


def test_env_var_override(tmp_path, monkeypatch):
    """Environment variables override LLM config."""
    config_path = tmp_path / "agent-mesh.toml"
    config_path.write_text("""
[llm]
provider = "original"
base_url = "http://original"
model = "original-model"
""")
    monkeypatch.setenv("SAM_LLM_PROVIDER", "override-provider")
    monkeypatch.setenv("SAM_LLM_MODEL", "override-model")

    config = load_config(tmp_path)
    assert config.llm.provider == "override-provider"
    assert config.llm.model == "override-model"
    # base_url not overridden
    assert config.llm.base_url == "http://original"


def test_auto_build_defaults(tmp_path):
    """Auto-build defaults to enabled with sane intervals."""
    config = _parse_config({}, tmp_path)
    assert config.auto_build.enabled is True
    assert config.auto_build.cooldown == 300
    assert config.auto_build.scan_interval == 60


def test_auto_build_custom(tmp_path):
    """Auto-build config is parsed from TOML."""
    raw = {
        "auto_build": {
            "enabled": False,
            "cooldown": 600,
            "scan_interval": 30,
        },
    }
    config = _parse_config(raw, tmp_path)
    assert config.auto_build.enabled is False
    assert config.auto_build.cooldown == 600
    assert config.auto_build.scan_interval == 30

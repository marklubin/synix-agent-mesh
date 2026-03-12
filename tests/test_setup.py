"""Tests for agent detection, MCP auto-configuration, and context injection."""

import json
from pathlib import Path

from synix_agent_mesh.setup import (
    KNOWN_AGENTS,
    MCP_SERVER_NAME,
    AgentInfo,
    _build_mcp_entry,
    check_api_key,
    configure_agent_mcp,
    count_sessions,
    detect_agents,
    inject_context,
)


def test_known_agents_have_required_fields():
    """All known agents have the required fields populated."""
    for agent in KNOWN_AGENTS:
        assert agent.name
        assert agent.display_name
        assert agent.detection_path
        assert agent.transport_type


def test_build_mcp_entry():
    """MCP entry has correct structure."""
    entry = _build_mcp_entry("streamable-http", "http://localhost:8200/mcp")
    assert entry == {
        "type": "streamable-http",
        "url": "http://localhost:8200/mcp",
    }


def test_configure_agent_mcp_creates_new_config(tmp_path):
    """Writes MCP config when no file exists."""
    config_path = tmp_path / "mcp.json"
    agent = AgentInfo(
        name="test-agent",
        display_name="Test Agent",
        detection_path=str(tmp_path),
        config_path=str(config_path),
        transport_type="streamable-http",
    )
    result = configure_agent_mcp(agent)
    assert "added" in result

    data = json.loads(config_path.read_text())
    assert MCP_SERVER_NAME in data["mcpServers"]
    assert data["mcpServers"][MCP_SERVER_NAME]["type"] == "streamable-http"


def test_configure_agent_mcp_merges_existing(tmp_path):
    """Preserves existing MCP servers when adding memory."""
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "existing-server": {"type": "stdio", "command": "test"}
        }
    }))
    agent = AgentInfo(
        name="test-agent",
        display_name="Test Agent",
        detection_path=str(tmp_path),
        config_path=str(config_path),
        transport_type="http",
    )
    result = configure_agent_mcp(agent)
    assert "added" in result

    data = json.loads(config_path.read_text())
    assert "existing-server" in data["mcpServers"]
    assert MCP_SERVER_NAME in data["mcpServers"]


def test_configure_agent_mcp_skips_if_exists(tmp_path):
    """Skips configuration if memory server already exists."""
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            MCP_SERVER_NAME: {"type": "streamable-http", "url": "http://other:9999/mcp"}
        }
    }))
    agent = AgentInfo(
        name="test-agent",
        display_name="Test Agent",
        detection_path=str(tmp_path),
        config_path=str(config_path),
        transport_type="streamable-http",
    )
    result = configure_agent_mcp(agent)
    assert result == "already configured"

    # Verify original URL was NOT overwritten
    data = json.loads(config_path.read_text())
    assert data["mcpServers"][MCP_SERVER_NAME]["url"] == "http://other:9999/mcp"


def test_configure_agent_mcp_dry_run(tmp_path):
    """Dry run does not write files."""
    config_path = tmp_path / "mcp.json"
    agent = AgentInfo(
        name="test-agent",
        display_name="Test Agent",
        detection_path=str(tmp_path),
        config_path=str(config_path),
        transport_type="streamable-http",
    )
    result = configure_agent_mcp(agent, dry_run=True)
    assert "would add" in result
    assert not config_path.exists()


def test_configure_agent_mcp_non_auto(tmp_path):
    """Non-auto-configurable agents return manual message."""
    agent = AgentInfo(
        name="manual-agent",
        display_name="Manual Agent",
        detection_path=str(tmp_path),
        config_path="",
        transport_type="http",
        auto_configurable=False,
    )
    result = configure_agent_mcp(agent)
    assert result == "manual config required"


def test_configure_agent_mcp_custom_url(tmp_path):
    """Custom MCP URL is used in config."""
    config_path = tmp_path / "mcp.json"
    agent = AgentInfo(
        name="test-agent",
        display_name="Test Agent",
        detection_path=str(tmp_path),
        config_path=str(config_path),
        transport_type="streamable-http",
    )
    configure_agent_mcp(agent, mcp_url="http://myserver:8200/mcp")

    data = json.loads(config_path.read_text())
    assert data["mcpServers"][MCP_SERVER_NAME]["url"] == "http://myserver:8200/mcp"


def test_count_sessions_empty(tmp_path):
    """Returns 0 for empty directory."""
    assert count_sessions(str(tmp_path)) == 0


def test_count_sessions_with_files(tmp_path):
    """Counts JSONL files recursively."""
    (tmp_path / "project-a").mkdir()
    (tmp_path / "project-a" / "session1.jsonl").write_text("{}")
    (tmp_path / "project-a" / "session2.jsonl").write_text("{}")
    (tmp_path / "project-b").mkdir()
    (tmp_path / "project-b" / "session3.jsonl").write_text("{}")
    (tmp_path / "project-b" / "not-a-session.txt").write_text("")

    assert count_sessions(str(tmp_path)) == 3


def test_count_sessions_missing_dir():
    """Returns 0 for non-existent directory."""
    assert count_sessions("/nonexistent/path/that/does/not/exist") == 0


def test_check_api_key_present(monkeypatch):
    """Detects when API key is set."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    has_key, msg = check_api_key()
    assert has_key is True
    assert "detected" in msg


def test_check_api_key_missing(monkeypatch):
    """Detects when API key is missing."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    has_key, msg = check_api_key()
    assert has_key is False
    assert "not set" in msg


def test_detect_agents_returns_all_known():
    """detect_agents returns an entry for every known agent."""
    results = detect_agents()
    assert len(results) == len(KNOWN_AGENTS)
    names = [agent.name for agent, _ in results]
    for known in KNOWN_AGENTS:
        assert known.name in names


# ---------------------------------------------------------------------------
# Context injection tests
# ---------------------------------------------------------------------------


def _make_agent(tmp_path, *, append=True, marker="## Memory (sam)"):
    """Helper to create an AgentInfo with context injection configured."""
    context_path = tmp_path / ("rules.md" if append else "sam-memory.mdc")
    return AgentInfo(
        name="test-agent",
        display_name="Test Agent",
        detection_path=str(tmp_path),
        config_path=str(tmp_path / "mcp.json"),
        transport_type="streamable-http",
        context_path=str(context_path),
        context_marker=marker if append else "",
        context_append=append,
        context_snippet="## Memory (sam)\n\nUse memory_search on session start.\n",
    )


def test_inject_context_append_new_file(tmp_path):
    """Appends context snippet to a new file."""
    agent = _make_agent(tmp_path, append=True)
    result = inject_context(agent)
    assert "added" in result

    content = Path(agent.context_path).read_text()
    assert "## Memory (sam)" in content
    assert "memory_search" in content


def test_inject_context_append_existing_no_marker(tmp_path):
    """Appends to existing file that lacks the marker."""
    agent = _make_agent(tmp_path, append=True)
    context_path = Path(agent.context_path)
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text("# Existing rules\n\nDo good stuff.\n")

    result = inject_context(agent)
    assert "added" in result

    content = context_path.read_text()
    assert content.startswith("# Existing rules")
    assert "## Memory (sam)" in content


def test_inject_context_append_idempotent(tmp_path):
    """Skips injection when marker already present."""
    agent = _make_agent(tmp_path, append=True)
    context_path = Path(agent.context_path)
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text("# Rules\n\n## Memory (sam)\n\nAlready here.\n")

    result = inject_context(agent)
    assert result == "memory instructions already present"

    # Content unchanged
    content = context_path.read_text()
    assert content.count("## Memory (sam)") == 1


def test_inject_context_append_dry_run(tmp_path):
    """Dry run reports what would happen without writing."""
    agent = _make_agent(tmp_path, append=True)
    result = inject_context(agent, dry_run=True)
    assert "would add" in result
    assert not Path(agent.context_path).exists()


def test_inject_context_create_new_file(tmp_path):
    """Creates context file in create mode."""
    agent = _make_agent(tmp_path, append=False)
    result = inject_context(agent)
    assert "created" in result

    content = Path(agent.context_path).read_text()
    assert "memory_search" in content


def test_inject_context_create_skips_existing(tmp_path):
    """Skips creation when file already exists."""
    agent = _make_agent(tmp_path, append=False)
    context_path = Path(agent.context_path)
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text("existing content")

    result = inject_context(agent)
    assert result == "memory rules already present"

    # Content unchanged
    assert context_path.read_text() == "existing content"


def test_inject_context_create_dry_run(tmp_path):
    """Dry run in create mode doesn't write."""
    agent = _make_agent(tmp_path, append=False)
    result = inject_context(agent, dry_run=True)
    assert "would create" in result
    assert not Path(agent.context_path).exists()


def test_inject_context_no_snippet():
    """Returns empty string when no context configured."""
    agent = AgentInfo(
        name="bare",
        display_name="Bare",
        detection_path="/tmp",
        config_path="/tmp/mcp.json",
        transport_type="http",
    )
    assert inject_context(agent) == ""


def test_known_agents_context_snippets():
    """Auto-configurable agents with context paths have non-empty snippets."""
    for agent in KNOWN_AGENTS:
        if agent.auto_configurable and agent.context_path:
            assert agent.context_snippet, f"{agent.name} has context_path but no snippet"
            assert "memory" in agent.context_snippet.lower(), (
                f"{agent.name} snippet doesn't mention memory"
            )

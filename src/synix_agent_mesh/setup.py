"""Agent detection, MCP auto-configuration, and context injection for `sam setup`."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------


@dataclass
class AgentInfo:
    """Detected coding agent."""

    name: str
    display_name: str
    detection_path: str  # directory to check for existence
    config_path: str  # MCP config file to write
    transport_type: str  # streamable-http, http, sse
    auto_configurable: bool = True
    manual_instructions: str = ""
    # Context injection
    context_path: str = ""  # behavioral config file
    context_marker: str = ""  # marker to detect existing injection
    context_append: bool = True  # True = append to file, False = create/replace
    context_snippet: str = ""  # the behavioral text to inject


# --- Context snippets per agent ---

_CLAUDE_CODE_SNIPPET = """\

## Memory (sam)

You have persistent cross-session memory via the `memory` MCP server.

**On session start:**
1. Call `memory_search` with the current project name or topic to recall relevant context
2. Call `memory_get_flat_file` with name "context-doc" for your synthesized understanding
3. Call `memory_get_flat_file` with name "work-status-doc" for active projects and blockers

**During work:**
- Search memory when you encounter unfamiliar code, past decisions, or recurring questions
- Memory covers all past sessions across all machines

**Never:**
- Call `memory_clean` or `memory_source_clear` without explicit user confirmation
- These are destructive and irreversible
"""

_CURSOR_SNIPPET = """\
---
description: Persistent agent memory via sam
globs:
alwaysApply: true
---

You have persistent memory via the `memory` MCP server.

At the start of each conversation:
- Use `memory_search` with the project name to recall relevant context
- Use `memory_get_flat_file("context-doc")` for synthesized project understanding
- Use `memory_get_flat_file("work-status-doc")` for active projects and blockers

Search memory when you encounter unfamiliar patterns, past decisions, or recurring questions.
Never call `memory_clean` or `memory_source_clear` without user confirmation.
"""

_WINDSURF_SNIPPET = """\

## Memory (sam)

You have persistent memory via the `memory` MCP server.

At conversation start, call memory_search with the project name to load relevant context.
Use memory_get_flat_file("context-doc") for synthesized understanding.
Use memory_get_flat_file("work-status-doc") for active work status.
Search memory when encountering unfamiliar code or past decisions.
Never call memory_clean or memory_source_clear without user confirmation.
"""

_ROO_SNIPPET = """\

## Memory (sam)

You have persistent memory via the `memory` MCP server.

On session start:
1. `memory_search` with project name for relevant context
2. `memory_get_flat_file("context-doc")` for synthesized understanding
3. `memory_get_flat_file("work-status-doc")` for active projects

Search memory for past decisions, recurring patterns, and unfamiliar code.
Never call `memory_clean` or `memory_source_clear` without user confirmation.
"""


# All known coding agents and their MCP config details
KNOWN_AGENTS: list[AgentInfo] = [
    AgentInfo(
        name="claude-code",
        display_name="Claude Code",
        detection_path="~/.claude",
        config_path="~/.claude/mcp.json",
        transport_type="streamable-http",
        context_path="~/.claude/CLAUDE.md",
        context_marker="## Memory (sam)",
        context_append=True,
        context_snippet=_CLAUDE_CODE_SNIPPET,
    ),
    AgentInfo(
        name="cursor",
        display_name="Cursor",
        detection_path="~/.cursor",
        config_path="~/.cursor/mcp.json",
        transport_type="http",
        context_path="~/.cursor/rules/sam-memory.mdc",
        context_marker="",
        context_append=False,  # create whole file
        context_snippet=_CURSOR_SNIPPET,
    ),
    AgentInfo(
        name="windsurf",
        display_name="Windsurf",
        detection_path="~/.codeium/windsurf",
        config_path="~/.codeium/windsurf/mcp_config.json",
        transport_type="streamable-http",
        context_path="~/.codeium/windsurf/.windsurfrules",
        context_marker="## Memory (sam)",
        context_append=True,
        context_snippet=_WINDSURF_SNIPPET,
    ),
    AgentInfo(
        name="roo-code",
        display_name="Roo Code",
        detection_path="~/.roo",
        config_path="~/.roo/mcp.json",
        transport_type="streamable-http",
        context_path="~/.roo/rules.md",
        context_marker="## Memory (sam)",
        context_append=True,
        context_snippet=_ROO_SNIPPET,
    ),
    AgentInfo(
        name="vscode",
        display_name="VS Code + Copilot",
        detection_path="~/.vscode",
        config_path="",
        transport_type="http",
        auto_configurable=False,
        manual_instructions=(
            "Settings → search 'MCP' → add server:\n"
            "    Name: memory\n"
            "    URL:  http://localhost:8200/mcp"
        ),
    ),
]

MCP_SERVER_NAME = "memory"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_agents() -> list[tuple[AgentInfo, bool]]:
    """Detect which coding agents are installed.

    Returns list of (agent_info, is_installed) tuples.
    """
    results = []
    for agent in KNOWN_AGENTS:
        path = Path(agent.detection_path).expanduser()
        results.append((agent, path.exists()))
    return results


def count_sessions(session_dir: str = "~/.claude/projects") -> int:
    """Count JSONL session files in a directory."""
    path = Path(session_dir).expanduser()
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob("*.jsonl"))


# ---------------------------------------------------------------------------
# MCP configuration
# ---------------------------------------------------------------------------


def _build_mcp_entry(transport_type: str, mcp_url: str) -> dict:
    """Build the MCP server entry for a given transport type."""
    return {
        "type": transport_type,
        "url": mcp_url,
    }


def configure_agent_mcp(
    agent: AgentInfo,
    mcp_url: str = "http://localhost:8200/mcp",
    dry_run: bool = False,
) -> str:
    """Configure MCP for a single agent. Returns status message.

    Never overwrites existing config — always merges.
    """
    if not agent.auto_configurable:
        return "manual config required"

    config_path = Path(agent.config_path).expanduser()

    # Read existing config or create empty structure
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            return f"could not read {config_path}: {exc}"
    else:
        existing = {}

    # Ensure mcpServers key exists
    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    # Check if already configured
    if MCP_SERVER_NAME in existing["mcpServers"]:
        return "already configured"

    # Add the memory server
    existing["mcpServers"][MCP_SERVER_NAME] = _build_mcp_entry(
        agent.transport_type, mcp_url
    )

    if dry_run:
        return f"would add '{MCP_SERVER_NAME}' to {config_path}"

    # Write back
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2) + "\n")

    return f"added '{MCP_SERVER_NAME}' server to {config_path}"


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------


def inject_context(agent: AgentInfo, dry_run: bool = False) -> str:
    """Inject behavioral instructions into agent's config.

    For append mode: reads file, checks for marker, appends if absent.
    For create mode: creates file if it doesn't exist, skips if it does.

    Never overwrites existing content. Always idempotent.
    Returns status message.
    """
    if not agent.context_path or not agent.context_snippet:
        return ""

    context_path = Path(agent.context_path).expanduser()

    if agent.context_append:
        # Append mode: check for marker in existing file
        existing = ""
        if context_path.exists():
            existing = context_path.read_text()
            if agent.context_marker and agent.context_marker in existing:
                return "memory instructions already present"

        if dry_run:
            return f"would add memory instructions to {context_path}"

        context_path.parent.mkdir(parents=True, exist_ok=True)
        with open(context_path, "a") as f:
            f.write(agent.context_snippet)

        return f"memory instructions added to {context_path}"

    else:
        # Create mode: write whole file, skip if exists
        if context_path.exists():
            return "memory rules already present"

        if dry_run:
            return f"would create {context_path}"

        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(agent.context_snippet)

        return f"memory rules created at {context_path}"


# ---------------------------------------------------------------------------
# API key check
# ---------------------------------------------------------------------------


def check_api_key() -> tuple[bool, str]:
    """Check if an LLM API key is available."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return True, "OPENAI_API_KEY detected"
    return False, "OPENAI_API_KEY not set (needed for builds)"


# ---------------------------------------------------------------------------
# Main setup flow
# ---------------------------------------------------------------------------


def run_setup(
    target_dir: str = ".",
    name: str = "agent-memory",
    mcp_url: str = "http://localhost:8200/mcp",
    agent_filter: str | None = None,
    skip_mcp: bool = False,
    dry_run: bool = False,
) -> bool:
    """Run the full setup flow. Returns True on success."""
    target = Path(target_dir).resolve()

    # --- Header ---
    console.print()
    console.print("[bold]sam[/bold] — persistent memory for your coding agent")
    console.print()
    console.print(
        "  Your coding sessions will be automatically watched, indexed,\n"
        "  and made searchable. Your agent will remember everything."
    )

    # --- Step 1: Detect agents ---
    console.print()
    console.print("  Detecting coding agents...")

    detected = detect_agents()
    installed_agents = []
    session_count = 0

    for agent, is_installed in detected:
        if is_installed:
            prefix = "    [green]✓[/green]"
            extra = ""
            if agent.name == "claude-code":
                session_count = count_sessions()
                if session_count > 0:
                    extra = f"  [dim]{session_count:,} sessions found[/dim]"
            console.print(f"{prefix} {agent.display_name}{extra}")
            installed_agents.append(agent)
        else:
            console.print(f"    [dim]·[/dim] {agent.display_name}  [dim]not installed[/dim]")

    # --- Step 2: Initialize project ---
    console.print()
    console.print("  Initializing project...")

    config_path = target / "agent-mesh.toml"
    if config_path.exists():
        console.print("    [green]✓[/green] agent-mesh.toml  [dim](already exists)[/dim]")
    else:
        if not dry_run:
            from synix_agent_mesh.cli import _default_config
            target.mkdir(parents=True, exist_ok=True)
            config_path.write_text(_default_config(name))
            (target / "sources").mkdir(exist_ok=True)
        console.print("    [green]✓[/green] Created agent-mesh.toml")

    # Initialize synix project
    synix_dir = target / ".synix"
    if synix_dir.exists():
        console.print("    [green]✓[/green] .synix/ initialized  [dim](already exists)[/dim]")
    else:
        if not dry_run:
            try:
                import synix
                synix.init(str(target))
                console.print("    [green]✓[/green] Initialized .synix/ project")
            except Exception as exc:
                console.print(f"    [yellow]![/yellow] synix init: {exc}")
        else:
            console.print("    [dim]Would initialize .synix/ project[/dim]")

    # Create mesh
    from synix.mesh.config import resolve_mesh_root
    mesh_dir = resolve_mesh_root() / name
    if mesh_dir.exists():
        console.print(f"    [green]✓[/green] Mesh '{name}' provisioned  [dim](already exists)[/dim]")
    else:
        if not dry_run:
            try:
                from synix.mesh.provision import create_mesh
                pipeline_path = target / "pipeline.py"
                if not pipeline_path.exists():
                    pipeline_path.write_text(
                        '"""Pipeline stub — real pipeline built dynamically from agent-mesh.toml."""\n'
                        "from synix import Pipeline\n"
                        'pipeline = Pipeline("agent-mesh")\n'
                    )
                create_mesh(name, pipeline_path, mesh_root=resolve_mesh_root())
                console.print(f"    [green]✓[/green] Created mesh '{name}'")
            except Exception as exc:
                console.print(f"    [yellow]![/yellow] Mesh creation: {exc}")
        else:
            console.print(f"    [dim]Would create mesh '{name}'[/dim]")

    # --- Step 3: Connect agents (MCP + context injection) ---
    if not skip_mcp:
        console.print()
        console.print("  Connecting your agents...")

        for agent in installed_agents:
            if agent_filter and agent.name != agent_filter:
                continue

            if agent.auto_configurable:
                # MCP configuration
                mcp_result = configure_agent_mcp(agent, mcp_url=mcp_url, dry_run=dry_run)
                console.print(f"    [green]✓[/green] {agent.display_name}  MCP {mcp_result}")

                # Context injection
                ctx_result = inject_context(agent, dry_run=dry_run)
                if ctx_result:
                    console.print(f"    [green]✓[/green] {agent.display_name}  {ctx_result}")
            else:
                console.print(
                    f"    [dim]·[/dim] {agent.display_name}  "
                    f"[dim]({agent.manual_instructions.splitlines()[0]})[/dim]"
                )

    # --- Step 4: Check LLM ---
    console.print()
    console.print("  LLM provider:")
    has_key, key_msg = check_api_key()
    if has_key:
        console.print(f"    [green]✓[/green] {key_msg}")
    else:
        console.print(f"    [yellow]![/yellow] {key_msg}")
    console.print("    [dim]Configurable in agent-mesh.toml[/dim]")

    # --- Step 5: Done ---
    console.print()
    console.print("  [green bold]Setup complete![/green bold]")
    console.print()
    console.print("  Next:")
    console.print("    sam serve          Start the memory server")
    console.print("    sam doctor         Verify everything works")

    if session_count > 0:
        console.print()
        console.print(
            Panel(
                f"  [bold]{session_count:,}[/bold] Claude Code sessions ready to process.\n"
                "\n"
                "  Start [bold]sam serve[/bold], then open Claude Code.\n"
                "  Your agent will automatically search its memory\n"
                "  at the start of every conversation.\n"
                "\n"
                '  Try asking: [italic]"what did I work on this week?"[/italic]',
                border_style="green",
            )
        )

    console.print()
    return True

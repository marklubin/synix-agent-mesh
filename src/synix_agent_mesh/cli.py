"""CLI for synix-agent-mesh — the agent capture layer."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
@click.version_option(package_name="synix-agent-mesh")
def cli():
    """synix-agent-mesh — cross-device agent memory mesh."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(message)s",
    )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--dir", "target_dir", default=".", help="Directory to initialize")
@click.option("--name", default="agent-memory", help="Mesh name")
def init(target_dir: str, name: str):
    """Initialize a new agent-mesh project."""
    target = Path(target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)

    config_path = target / "agent-mesh.toml"
    if config_path.exists():
        console.print(f"[yellow]agent-mesh.toml already exists in {target}[/yellow]")
        return

    # Generate config with the correct mesh name
    config_path.write_text(_default_config(name))

    # Create sources directory
    (target / "sources").mkdir(exist_ok=True)

    # Initialize synix project
    try:
        import synix
        synix.init(str(target))
        console.print(f"[green]Initialized synix project in {target}[/green]")
    except Exception as exc:
        console.print(f"[yellow]synix init skipped: {exc}[/yellow]")

    # Create mesh
    try:
        from synix.mesh.config import resolve_mesh_root
        from synix.mesh.provision import create_mesh

        # Write a minimal pipeline.py for mesh creation
        pipeline_path = target / "pipeline.py"
        if not pipeline_path.exists():
            pipeline_path.write_text(
                '"""Pipeline stub — real pipeline built dynamically from agent-mesh.toml."""\n'
                "from synix import Pipeline\n"
                'pipeline = Pipeline("agent-mesh")\n'
            )

        config = create_mesh(name, pipeline_path, mesh_root=resolve_mesh_root())
        console.print(f"[green]Created mesh '[bold]{name}[/bold]'[/green]")
        console.print(f"  Token: {config.token[:12]}...")
    except Exception as exc:
        console.print(f"[yellow]Mesh creation skipped: {exc}[/yellow]")

    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print(f"  cd {target}")
    console.print("  sam serve                    # start mesh server + viewer")
    console.print("  sam serve --no-viewer         # mesh server only")
    console.print()
    console.print("[bold]On other machines:[/bold]")
    console.print(f"  sam join --server HOST:{_default_port()}")

    # Print MCP config hint
    console.print()
    console.print("[bold]MCP for Claude Code:[/bold]")
    _print_mcp_config(target)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--viewer/--no-viewer", default=True, help="Start viewer (default: on)")
@click.option("--mcp/--no-mcp", default=True, help="Start MCP HTTP server (default: on)")
@click.option("--mcp-port", default=8200, type=int, help="MCP HTTP server port (default: 8200)")
def serve(viewer: bool, mcp: bool, mcp_port: int):
    """Start mesh server + viewer + MCP HTTP server."""
    from synix_agent_mesh.config import load_config
    from synix_agent_mesh.server import serve as _serve

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[green]Starting agent-mesh '[bold]{config.mesh.name}[/bold]'[/green]")
    console.print(f"  Mesh server:    0.0.0.0:{config.mesh.port}")
    if mcp:
        console.print(f"  MCP HTTP:       0.0.0.0:{mcp_port}")
    console.print("  Local client:   watching ~/.claude/projects")
    if viewer:
        console.print(f"  Viewer:         http://{config.viewer.host}:{config.viewer.port}")
    console.print()

    if mcp:
        console.print("[bold]Remote MCP config:[/bold]")
        _print_remote_mcp_config(mcp_port)
        console.print()

    asyncio.run(_serve(config, viewer=viewer, mcp=mcp, mcp_port=mcp_port))


# ---------------------------------------------------------------------------
# join
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--server", "server_url", required=True, help="Server address (e.g., myhost:7433)")
@click.option("--name", default=None, help="Mesh name (auto-detected if omitted)")
def join(server_url: str, name: str | None):
    """Join an existing mesh as a client."""
    from synix.mesh.client import MeshClient
    from synix.mesh.config import load_mesh_config, resolve_mesh_root
    from synix.mesh.logging import setup_mesh_logging
    from synix.mesh.provision import provision_role

    if not server_url.startswith("http"):
        server_url = f"http://{server_url}"

    if name is None:
        # Try to detect from current directory config
        try:
            from synix_agent_mesh.config import load_config
            config = load_config()
            name = config.mesh.name
        except FileNotFoundError:
            console.print("[red]Error:[/red] --name required (no agent-mesh.toml found)")
            sys.exit(1)

    mesh_root = resolve_mesh_root()
    config_path = mesh_root / name / "synix-mesh.toml"

    if not config_path.exists():
        console.print(f"[red]Error:[/red] Mesh '{name}' not found. Run 'sam init --name {name}' first.")
        sys.exit(1)

    try:
        provision_role(name, "client", server_url=server_url, mesh_root=mesh_root)
        console.print(f"[green]Provisioned as client for mesh '[bold]{name}[/bold]'[/green]")
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    mesh_config = load_mesh_config(config_path)
    setup_mesh_logging(
        mesh_config.mesh_dir,
        "client",
        file_level=mesh_config.logging_config.get_file_level(),
        stderr_level=mesh_config.logging_config.get_stderr_level(),
    )

    client = MeshClient(mesh_config)
    console.print(f"  Server: {server_url}")
    console.print(f"  Watch dir: {mesh_config.source.watch_dir}")
    console.print("[green]Starting client daemon...[/green]")

    asyncio.run(client.start())


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--force", is_flag=True, help="Force rebuild even if no new sources")
@click.option("--local", is_flag=True, help="Build locally (skip mesh server)")
@click.option("-v", "--verbose", count=True, help="Verbosity level")
def build(force: bool, local: bool, verbose: int):
    """Trigger a build."""
    from synix_agent_mesh.config import load_config

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if local:
        _build_local(config, verbose)
    else:
        _build_remote(config, force)


def _build_local(config, verbose: int):
    """Run a local synix build."""
    from synix_agent_mesh.pipeline import build_pipeline

    pipeline = build_pipeline(config)

    import synix
    project = synix.open_project(str(config.project_dir))
    project.set_pipeline(pipeline)

    console.print("[green]Building...[/green]")
    result = project.build()
    console.print(f"[green]Build complete: {result.built} built, {result.cached} cached[/green]")

    # Auto-release
    project.release_to("local")
    console.print("[green]Released to 'local'[/green]")


def _build_remote(config, force: bool):
    """Trigger build on the mesh server."""
    import httpx
    from synix.mesh.auth import auth_headers
    from synix.mesh.config import load_mesh_config, resolve_mesh_root

    config_path = resolve_mesh_root() / config.mesh.name / "synix-mesh.toml"
    if not config_path.exists():
        console.print("[yellow]No mesh configured, falling back to local build[/yellow]")
        _build_local(config, verbose=0)
        return

    mesh_config = load_mesh_config(config_path)
    state_path = resolve_mesh_root() / config.mesh.name / "state.json"
    server_url = ""
    if state_path.exists():
        state = json.loads(state_path.read_text())
        server_url = state.get("server_url", "")

    if not server_url:
        console.print("[yellow]No server URL, falling back to local build[/yellow]")
        _build_local(config, verbose=0)
        return

    headers = auth_headers(mesh_config.token)
    try:
        resp = httpx.post(f"{server_url}/api/v1/builds/trigger", headers=headers, timeout=30)
        if resp.status_code == 200:
            console.print("[green]Build started on server[/green]")
        elif resp.status_code == 202:
            console.print("[yellow]Build already in progress — queued for next run[/yellow]")
        else:
            console.print(f"[red]Trigger failed:[/red] {resp.status_code} {resp.text}")
            sys.exit(1)
    except httpx.ConnectError:
        console.print(f"[red]Error:[/red] Cannot connect to server at {server_url}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
def status():
    """Show mesh health and build status."""
    from synix.mesh.config import resolve_mesh_root

    from synix_agent_mesh.config import load_config

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    mesh_dir = resolve_mesh_root() / config.mesh.name
    if not mesh_dir.exists():
        console.print(f"[yellow]Mesh '{config.mesh.name}' not provisioned[/yellow]")
        return

    state_path = mesh_dir / "state.json"
    if not state_path.exists():
        console.print("[yellow]Mesh exists but not provisioned[/yellow]")
        return

    state = json.loads(state_path.read_text())

    console.print(f"[bold]Agent Mesh: {config.mesh.name}[/bold]")
    console.print(f"  Role:     {state.get('role', 'unknown')}")
    console.print(f"  Server:   {state.get('server_url', 'none')}")
    console.print(f"  Hostname: {state.get('my_hostname', 'unknown')}")
    console.print(f"  Term:     {state.get('term', {}).get('counter', 0)}")
    console.print(f"  Leader:   {state.get('term', {}).get('leader_id', 'none')}")

    # Sources
    console.print()
    console.print("[bold]Sources:[/bold]")
    for src in config.sources:
        status_icon = "[green]+[/green]" if src.enabled else "[dim]-[/dim]"
        exists = src.resolved_dir.exists()
        dir_status = "" if exists else " [red](missing)[/red]"
        console.print(f"  {status_icon} {src.name}: {src.dir}{dir_status}")

    # Try to query server
    if state.get("role") == "server":
        server_url = state.get("server_url", "")
        if server_url:
            try:
                import httpx
                from synix.mesh.auth import auth_headers
                from synix.mesh.config import load_mesh_config

                mesh_config = load_mesh_config(mesh_dir / "synix-mesh.toml")
                headers = auth_headers(mesh_config.token)
                resp = httpx.get(f"{server_url}/api/v1/status", headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    console.print()
                    console.print("[bold]Server:[/bold]")
                    console.print(f"  Builds:   {data.get('build_count', 0)}")
                    sessions = data.get("sessions", {})
                    console.print(f"  Sessions: {sessions.get('total', 0)} total, {sessions.get('pending', 0)} pending")
                    console.print(f"  Members:  {', '.join(data.get('members', []))}")
            except Exception:
                console.print("  [dim](server not reachable)[/dim]")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("query")
@click.option("--mode", default="keyword", type=click.Choice(["keyword", "semantic", "hybrid"]))
@click.option("--limit", default=10, help="Max results")
@click.option("--release", "release_name", default="local", help="Release to search")
def search(query: str, mode: str, limit: int, release_name: str):
    """Search agent memory."""
    from synix_agent_mesh.config import load_config

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    import synix
    try:
        project = synix.open_project(str(config.project_dir))
        release = project.release(release_name)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    try:
        results = release.search(query, mode=mode, limit=limit)
    except Exception as exc:
        console.print(f"[red]Search failed:[/red] {exc}")
        sys.exit(1)

    if not results:
        console.print("[dim]No results[/dim]")
        return

    for i, r in enumerate(results, 1):
        label = getattr(r, "label", str(r))
        layer = getattr(r, "layer", "")
        content = getattr(r, "content", "")
        score = getattr(r, "score", None)
        metadata = getattr(r, "metadata", {}) or {}

        title = metadata.get("title", label)
        date = metadata.get("date", "")

        header = f"[bold]{i}. {title}[/bold]"
        if layer:
            header += f" [dim]({layer})[/dim]"
        if date:
            header += f" [dim]{date}[/dim]"
        if score is not None:
            header += f" [dim]score={score:.3f}[/dim]"
        console.print(header)

        if content:
            # Show first 200 chars as snippet
            snippet = content[:200].replace("\n", " ").strip()
            if len(content) > 200:
                snippet += "..."
            console.print(f"   {snippet}")
        console.print()


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


@cli.group()
def sources():
    """Manage configured data sources."""
    pass


@sources.command("list")
def sources_list():
    """List all configured sources."""
    from synix_agent_mesh.config import load_config

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if not config.sources:
        console.print("[dim]No sources configured[/dim]")
        return

    table = Table(title="Sources")
    table.add_column("Name", style="bold")
    table.add_column("Directory")
    table.add_column("Patterns")
    table.add_column("Status")
    table.add_column("Description", style="dim")

    for src in config.sources:
        exists = src.resolved_dir.exists()
        if not src.enabled:
            status = "[dim]disabled[/dim]"
        elif exists:
            status = "[green]ok[/green]"
        else:
            status = "[red]missing[/red]"

        table.add_row(
            src.name,
            src.dir,
            ", ".join(src.patterns),
            status,
            src.description,
        )

    console.print(table)


@sources.command("add")
@click.argument("name")
@click.argument("directory")
@click.option("--patterns", default="**/*", help="Glob patterns (comma-separated)")
@click.option("--description", default="", help="Source description")
def sources_add(name: str, directory: str, patterns: str, description: str):
    """Add a new source to agent-mesh.toml."""
    from synix_agent_mesh.config import CONFIG_FILENAME

    config_path = Path.cwd() / CONFIG_FILENAME
    if not config_path.exists():
        console.print(f"[red]Error:[/red] No {CONFIG_FILENAME} found. Run 'sam init' first.")
        sys.exit(1)

    # Append source block to config
    pattern_list = [p.strip() for p in patterns.split(",")]
    patterns_toml = ", ".join(f'"{p}"' for p in pattern_list)

    block = f'\n[sources.{name}]\ndir = "{directory}"\npatterns = [{patterns_toml}]\ndescription = "{description}"\n'

    with open(config_path, "a") as f:
        f.write(block)

    console.print(f"[green]Added source '[bold]{name}[/bold]' → {directory}[/green]")


@sources.command("disable")
@click.argument("name")
def sources_disable(name: str):
    """Disable a source (set enabled = false)."""
    from synix_agent_mesh.config import CONFIG_FILENAME

    config_path = Path.cwd() / CONFIG_FILENAME
    if not config_path.exists():
        console.print(f"[red]Error:[/red] No {CONFIG_FILENAME} found")
        sys.exit(1)

    content = config_path.read_text()
    marker = f"[sources.{name}]"
    if marker not in content:
        console.print(f"[red]Error:[/red] Source '{name}' not found in config")
        sys.exit(1)

    # Insert enabled = false after the section header
    if "enabled = false" not in content.split(marker)[1].split("[")[0]:
        content = content.replace(marker, f"{marker}\nenabled = false")
        config_path.write_text(content)

    console.print(f"[green]Disabled source '[bold]{name}[/bold]'[/green]")


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--release", "release_name", default="local", help="Release to view (default: local)")
def view(release_name: str):
    """Open the memory viewer in browser."""
    import threading
    import time
    import webbrowser

    from synix_agent_mesh.config import load_config

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    import synix
    try:
        project = synix.open_project(str(config.project_dir))
        release = project.release(release_name)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        console.print("Run 'sam build --local' first to create a release.")
        sys.exit(1)

    try:
        from synix.viewer import serve as viewer_serve
    except ImportError:
        console.print("[red]Error:[/red] synix\\[viewer] extra not installed. Install with: pip install 'synix\\[viewer]'")
        sys.exit(1)

    # Use 127.0.0.1 for the browser URL even if bind host is 0.0.0.0
    browser_host = "127.0.0.1" if config.viewer.host == "0.0.0.0" else config.viewer.host
    url = f"http://{browser_host}:{config.viewer.port}"
    console.print(f"[green]Starting viewer at {url}[/green]")
    console.print(f"  Release: {release_name}")

    # Open browser after a short delay so the server can bind first
    def _open_browser():
        time.sleep(1)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()

    viewer_serve(
        release,
        host=config.viewer.host,
        port=config.viewer.port,
        title=config.mesh.name,
        project=project,
    )


# ---------------------------------------------------------------------------
# mcp-config
# ---------------------------------------------------------------------------


@cli.command("mcp-config")
def mcp_config():
    """Print MCP server configuration for Claude Code."""
    from synix_agent_mesh.config import load_config

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    _print_mcp_config(config.project_dir)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_port() -> int:
    return 7433


def _default_config(name: str) -> str:
    return f'''# synix-agent-mesh configuration

[mesh]
name = "{name}"
port = 7433

[viewer]
port = 9471
host = "127.0.0.1"

[sources.sessions]
dir = "~/.claude/projects"
patterns = ["**/*.jsonl"]
description = "Live Claude Code sessions"

[sources.exports]
dir = "./sources"
description = "Static ChatGPT/Claude exports"

[llm]
provider = "openai-compatible"
base_url = "https://api.kimi.com/coding/v1"
model = "kimi-for-coding"
temperature = 0.3
max_tokens = 8192

[pipeline]
weekly_rollup = true
work_status = true
context_budget = 10000

[deploy]
server_commands = []
client_commands = []
'''


def _print_mcp_config(project_dir: Path):
    """Print MCP configuration JSON for Claude Code (local stdio)."""
    mcp_json = {
        "mcpServers": {
            "agent-mesh": {
                "command": "uv",
                "args": [
                    "--directory", str(project_dir),
                    "run", "python", "-m", "synix.mcp",
                ],
                "env": {
                    "SYNIX_PROJECT": str(project_dir),
                },
            }
        }
    }
    console.print("Add to your Claude Code MCP settings (local/stdio):")
    console.print()
    console.print_json(json.dumps(mcp_json, indent=2))


def _print_remote_mcp_config(mcp_port: int):
    """Print MCP configuration JSON for remote HTTP access."""
    import socket
    hostname = socket.gethostname()

    mcp_json = {
        "mcpServers": {
            "agent-mesh": {
                "type": "streamable-http",
                "url": f"http://{hostname}:{mcp_port}/mcp/",
            }
        }
    }
    console.print_json(json.dumps(mcp_json, indent=2))
    console.print(f"[dim]Replace '{hostname}' with the Tailscale/IP address if needed[/dim]")

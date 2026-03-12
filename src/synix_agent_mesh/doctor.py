"""Health check engine for `sam doctor`."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from synix_agent_mesh.config import AgentMeshConfig, load_config
from synix_agent_mesh.setup import KNOWN_AGENTS, MCP_SERVER_NAME

console = Console()


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    status: str  # "pass", "fail", "warn"
    message: str
    fix: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class CategoryResult:
    """Results for an entire check category."""

    name: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(c.status == "fail" for c in self.checks):
            return "fail"
        if any(c.status == "warn" for c in self.checks):
            return "warn"
        return "pass"


def _icon(status: str) -> str:
    if status == "pass":
        return "[green]✓[/green]"
    if status == "fail":
        return "[red]✗[/red]"
    return "[yellow]![/yellow]"


# ---------------------------------------------------------------------------
# Check categories
# ---------------------------------------------------------------------------


def check_project(config: AgentMeshConfig) -> CategoryResult:
    """Check project structure."""
    cat = CategoryResult("Project")

    # Config file
    config_path = config.project_dir / "agent-mesh.toml"
    if config_path.exists():
        cat.checks.append(CheckResult("config", "pass", "agent-mesh.toml"))
    else:
        cat.checks.append(CheckResult("config", "fail", "agent-mesh.toml missing", fix="sam setup"))

    # Synix project
    synix_dir = config.project_dir / ".synix"
    if synix_dir.exists():
        cat.checks.append(CheckResult("synix", "pass", ".synix/ initialized"))
    else:
        cat.checks.append(CheckResult("synix", "fail", ".synix/ not initialized", fix="sam setup"))

    # Mesh provisioned
    try:
        from synix.mesh.config import resolve_mesh_root
        mesh_dir = resolve_mesh_root() / config.mesh.name
        if mesh_dir.exists():
            # Check role
            state_path = mesh_dir / "state.json"
            role = "unknown"
            if state_path.exists():
                state = json.loads(state_path.read_text())
                role = state.get("role", "unknown")
            cat.checks.append(
                CheckResult("mesh", "pass", f"Mesh '{config.mesh.name}' provisioned ({role})")
            )
        else:
            cat.checks.append(
                CheckResult("mesh", "fail", f"Mesh '{config.mesh.name}' not provisioned", fix="sam setup")
            )
    except Exception as exc:
        cat.checks.append(CheckResult("mesh", "warn", f"Could not check mesh: {exc}"))

    return cat


def check_sources(config: AgentMeshConfig) -> CategoryResult:
    """Check configured data sources."""
    cat = CategoryResult("Sources")

    if not config.sources:
        cat.checks.append(CheckResult("sources", "warn", "No sources configured"))
        return cat

    for src in config.sources:
        if not src.enabled:
            cat.checks.append(CheckResult(src.name, "pass", f"{src.dir}  [dim]disabled[/dim]"))
            continue

        resolved = src.resolved_dir
        if not resolved.exists():
            cat.checks.append(
                CheckResult(
                    src.name,
                    "warn",
                    f"{src.dir}  directory missing",
                    fix=f"sam sources disable {src.name}",
                )
            )
            continue

        # Count files matching patterns
        file_count = 0
        for pattern in src.patterns:
            file_count += sum(1 for _ in resolved.rglob(pattern.replace("**/", "")))

        cat.checks.append(
            CheckResult(
                src.name,
                "pass",
                f"{src.dir}",
                details={"file_count": file_count},
            )
        )

    return cat


def check_build(config: AgentMeshConfig) -> CategoryResult:
    """Check build state."""
    cat = CategoryResult("Build")

    synix_dir = config.project_dir / ".synix"
    if not synix_dir.exists():
        cat.checks.append(CheckResult("build", "fail", "No .synix/ project", fix="sam setup"))
        return cat

    # Check for releases
    releases_dir = synix_dir / "releases"
    if not releases_dir.exists():
        cat.checks.append(
            CheckResult("build", "warn", "No builds yet", fix="sam build --local")
        )
        return cat

    releases = [d for d in releases_dir.iterdir() if d.is_dir()]
    if not releases:
        cat.checks.append(
            CheckResult("build", "warn", "No releases found", fix="sam build --local")
        )
        return cat

    # Check local release specifically
    local_release = releases_dir / "local"
    if local_release.exists():
        manifest_path = local_release / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                artifact_count = len(manifest.get("artifacts", {}))
                built_at = manifest.get("built_at", "unknown")
                cat.checks.append(
                    CheckResult(
                        "release",
                        "pass",
                        f"'local' release: {artifact_count} artifacts",
                        details={"built_at": built_at, "artifact_count": artifact_count},
                    )
                )
            except (json.JSONDecodeError, OSError):
                cat.checks.append(CheckResult("release", "warn", "Could not read manifest"))
        else:
            cat.checks.append(
                CheckResult("release", "warn", "Release exists but no manifest", fix="sam build --local")
            )
    else:
        cat.checks.append(
            CheckResult("build", "warn", "No 'local' release", fix="sam build --local")
        )

    # Check violations
    violations_path = synix_dir / "violations_state.json"
    if violations_path.exists():
        try:
            violations = json.loads(violations_path.read_text())
            active = [v for v in violations.get("violations", []) if v.get("active", True)]
            if active:
                cat.checks.append(
                    CheckResult("violations", "warn", f"{len(active)} active violations")
                )
            else:
                cat.checks.append(CheckResult("violations", "pass", "No active violations"))
        except (json.JSONDecodeError, OSError):
            pass

    return cat


def check_search(config: AgentMeshConfig) -> CategoryResult:
    """Check search index state."""
    cat = CategoryResult("Search")

    synix_dir = config.project_dir / ".synix"
    releases_dir = synix_dir / "releases" / "local"

    if not releases_dir.exists():
        cat.checks.append(
            CheckResult("index", "warn", "No release to check", fix="sam build --local")
        )
        return cat

    # Look for search database
    search_db = releases_dir / "search.db"
    if not search_db.exists():
        # Try alternative locations
        search_db = synix_dir / "search.db"

    if search_db.exists():
        size_kb = search_db.stat().st_size / 1024
        cat.checks.append(
            CheckResult("index", "pass", f"Search index: {size_kb:.0f} KB")
        )
    else:
        cat.checks.append(
            CheckResult("index", "warn", "No search index found", fix="sam build --local")
        )

    return cat


def check_mcp(config: AgentMeshConfig, mcp_port: int = 8200) -> CategoryResult:
    """Check MCP server and agent configurations."""
    cat = CategoryResult("MCP")

    # Check if server is responding
    try:
        import httpx
        resp = httpx.get(f"http://localhost:{mcp_port}/mcp/", timeout=3)
        if resp.status_code in (200, 405):
            cat.checks.append(
                CheckResult("server", "pass", f"http://localhost:{mcp_port}/mcp (responding)")
            )
        else:
            cat.checks.append(
                CheckResult("server", "warn", f"MCP returned {resp.status_code}", fix="sam serve")
            )
    except Exception:
        cat.checks.append(
            CheckResult("server", "warn", "MCP server not reachable", fix="sam serve")
        )

    # Check agent configs
    for agent in KNOWN_AGENTS:
        if not agent.auto_configurable:
            continue

        config_path = Path(agent.config_path).expanduser()
        detection_path = Path(agent.detection_path).expanduser()

        if not detection_path.exists():
            continue  # Agent not installed, skip

        if not config_path.exists():
            cat.checks.append(
                CheckResult(
                    agent.name,
                    "warn",
                    f"{agent.display_name}: no MCP config",
                    fix="sam setup",
                )
            )
            continue

        try:
            data = json.loads(config_path.read_text())
            servers = data.get("mcpServers", {})
            if MCP_SERVER_NAME in servers:
                cat.checks.append(
                    CheckResult(agent.name, "pass", f"{agent.display_name}: configured in {agent.config_path}")
                )
            else:
                cat.checks.append(
                    CheckResult(
                        agent.name,
                        "warn",
                        f"{agent.display_name}: MCP config exists but no '{MCP_SERVER_NAME}' server",
                        fix="sam setup",
                    )
                )
        except (json.JSONDecodeError, OSError) as exc:
            cat.checks.append(
                CheckResult(agent.name, "warn", f"{agent.display_name}: could not read config: {exc}")
            )

    return cat


def check_llm(config: AgentMeshConfig, test_connectivity: bool = True) -> CategoryResult:
    """Check LLM provider configuration."""
    cat = CategoryResult("LLM")

    cat.checks.append(
        CheckResult(
            "provider",
            "pass",
            f"Provider: {config.llm.provider} ({config.llm.model})",
        )
    )

    # API key
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        cat.checks.append(CheckResult("api_key", "pass", "API key: set"))
    else:
        cat.checks.append(
            CheckResult("api_key", "warn", "API key: not set", fix="export OPENAI_API_KEY=...")
        )

    # Connectivity test
    if test_connectivity and api_key:
        try:
            import openai
            client = openai.OpenAI(
                api_key=api_key,
                base_url=config.llm.base_url,
                timeout=10,
            )
            resp = client.chat.completions.create(
                model=config.llm.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            cat.checks.append(CheckResult("connectivity", "pass", "Connectivity: OK"))
        except Exception as exc:
            cat.checks.append(
                CheckResult("connectivity", "warn", f"Connectivity: {exc}")
            )

    return cat


def check_mesh(config: AgentMeshConfig) -> CategoryResult:
    """Check mesh networking state."""
    cat = CategoryResult("Mesh")

    try:
        from synix.mesh.config import resolve_mesh_root
        mesh_dir = resolve_mesh_root() / config.mesh.name

        if not mesh_dir.exists():
            cat.checks.append(
                CheckResult("provisioned", "fail", "Not provisioned", fix="sam setup")
            )
            return cat

        state_path = mesh_dir / "state.json"
        if not state_path.exists():
            cat.checks.append(
                CheckResult("provisioned", "warn", "Provisioned but no state file")
            )
            return cat

        state = json.loads(state_path.read_text())
        role = state.get("role", "unknown")
        cat.checks.append(CheckResult("role", "pass", f"Role: {role}"))

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
                    members = data.get("members", [])
                    cat.checks.append(
                        CheckResult(
                            "server",
                            "pass",
                            f"Server reachable ({len(members)} member(s))",
                            details={"members": members},
                        )
                    )
                else:
                    cat.checks.append(
                        CheckResult("server", "warn", f"Server returned {resp.status_code}")
                    )
            except Exception:
                cat.checks.append(
                    CheckResult("server", "warn", "Server not reachable", fix="sam serve")
                )
        else:
            cat.checks.append(CheckResult("server", "warn", "No server URL configured"))

    except Exception as exc:
        cat.checks.append(CheckResult("mesh", "warn", f"Could not check mesh: {exc}"))

    return cat


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

ALL_CATEGORIES = {
    "project": check_project,
    "sources": check_sources,
    "build": check_build,
    "search": check_search,
    "mcp": check_mcp,
    "llm": check_llm,
    "mesh": check_mesh,
}


def run_doctor(
    config: AgentMeshConfig,
    categories: list[str] | None = None,
    json_output: bool = False,
    test_llm: bool = True,
    mcp_port: int = 8200,
) -> list[CategoryResult]:
    """Run all health checks. Returns list of category results."""

    targets = categories or list(ALL_CATEGORIES.keys())
    results: list[CategoryResult] = []

    for cat_name in targets:
        check_fn = ALL_CATEGORIES.get(cat_name)
        if check_fn is None:
            continue

        if cat_name == "llm":
            result = check_fn(config, test_connectivity=test_llm)
        elif cat_name == "mcp":
            result = check_fn(config, mcp_port=mcp_port)
        else:
            result = check_fn(config)

        results.append(result)

    if json_output:
        _print_json(results)
    else:
        _print_rich(results)

    return results


def _print_rich(results: list[CategoryResult]):
    """Print results in human-readable format."""
    console.print()
    console.print("[bold]sam doctor[/bold] — checking your setup")
    console.print()

    total_issues = 0

    for cat in results:
        console.print(f"[bold]{cat.name}[/bold]")
        for check in cat.checks:
            icon = _icon(check.status)
            extra = ""
            if check.details.get("file_count") is not None:
                extra = f"  [dim]{check.details['file_count']} files[/dim]"
            console.print(f"  {icon} {check.message}{extra}")
            if check.status in ("fail", "warn") and check.fix:
                console.print(f"    → {check.fix}")
                total_issues += 1
        console.print()

    console.print("─" * 30)
    if total_issues > 0:
        console.print(f"{total_issues} issue(s) found.")
    else:
        console.print("[green]All checks passed.[/green]")
    console.print()


def _print_json(results: list[CategoryResult]):
    """Print results as JSON."""
    overall = "pass"
    issues = []

    output = {"status": "pass", "checks": {}, "issues": []}

    for cat in results:
        cat_data = {
            "status": cat.status,
            "details": {},
        }
        for check in cat.checks:
            cat_data["details"][check.name] = {
                "status": check.status,
                "message": check.message,
                **check.details,
            }
            if check.status in ("fail", "warn"):
                if overall == "pass":
                    overall = check.status
                elif check.status == "fail":
                    overall = "fail"
                issues.append({
                    "category": cat.name,
                    "severity": check.status,
                    "message": check.message,
                    "fix": check.fix,
                })

        output["checks"][cat.name.lower()] = cat_data

    output["status"] = overall
    output["issues"] = issues

    console.print_json(json.dumps(output, indent=2))

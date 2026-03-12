"""Server orchestrator — runs mesh server + viewer + MCP HTTP on separate ports."""

from __future__ import annotations

import asyncio
import logging
import signal

from synix_agent_mesh.config import AgentMeshConfig

logger = logging.getLogger(__name__)

# Default MCP HTTP port
MCP_DEFAULT_PORT = 8200


async def run_mesh_server(config: AgentMeshConfig) -> None:
    """Start the synix mesh server on the configured port."""
    import uvicorn
    from synix.mesh.config import load_mesh_config, resolve_mesh_root
    from synix.mesh.logging import setup_mesh_logging
    from synix.mesh.server import create_app

    config_path = resolve_mesh_root() / config.mesh.name / "synix-mesh.toml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Mesh '{config.mesh.name}' not found. Run 'sam init' first."
        )

    mesh_config = load_mesh_config(config_path)
    setup_mesh_logging(
        mesh_config.mesh_dir,
        "server",
        file_level=mesh_config.logging_config.get_file_level(),
        stderr_level=mesh_config.logging_config.get_stderr_level(),
    )
    app = create_app(mesh_config)

    uv_config = uvicorn.Config(
        app, host="0.0.0.0", port=config.mesh.port, log_level="info",
    )
    server = uvicorn.Server(uv_config)
    await server.serve()


async def run_mcp_http(config: AgentMeshConfig, port: int = MCP_DEFAULT_PORT) -> None:
    """Start the synix MCP server over HTTP (Streamable HTTP transport).

    This makes the MCP server accessible over the network so remote
    machines (Oxnard, Obispo) can connect to it.
    """
    # Auto-open the project
    import synix
    import uvicorn

    # Import and configure the synix MCP server
    from synix.mcp.server import _state, mcp
    project = synix.open_project(str(config.project_dir))
    _state["project"] = project
    logger.info("MCP: opened project at %s", config.project_dir)

    # Load the pipeline so build/source tools work
    pipeline_path = config.project_dir / "pipeline.py"
    if pipeline_path.exists():
        try:
            project.load_pipeline(str(pipeline_path))
            logger.info("MCP: loaded pipeline from %s", pipeline_path)
        except Exception as exc:
            logger.warning("MCP: could not load pipeline: %s", exc)

    # Allow remote hosts (Tailscale hostnames) for DNS rebinding protection
    import socket

    from mcp.server.transport_security import TransportSecuritySettings
    hostname = socket.gethostname()
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            f"localhost:{port}",
            f"127.0.0.1:{port}",
            f"{hostname}:{port}",
            f"{hostname}:*",
            # Tailscale hostnames
            "salinas:*",
            "oxnard:*",
            "obispo:*",
        ],
    )

    # Get the Starlette app for HTTP transport
    app = mcp.streamable_http_app()

    uv_config = uvicorn.Config(
        app, host="0.0.0.0", port=port, log_level="info",
    )
    server = uvicorn.Server(uv_config)
    await server.serve()


def _resolve_viewer_release(project):
    """Try to find a usable release for the viewer, returning the first that exists."""
    for name in project.releases():
        return project.release(name)
    return None


def run_viewer(config: AgentMeshConfig) -> None:
    """Start the synix viewer Flask app (blocking, meant for thread)."""
    try:
        import synix
        from synix.viewer import serve as viewer_serve
    except ImportError:
        logger.warning("Viewer: synix[viewer] extra not installed — viewer disabled")
        return

    try:
        project = synix.open_project(str(config.project_dir))
        release = _resolve_viewer_release(project)

        if release is None:
            logger.warning("Viewer: no releases found — run 'sam build --local' to populate")
            return

        logger.info("Viewer: serving release '%s'", release.name)
        viewer_serve(
            release,
            host=config.viewer.host,
            port=config.viewer.port,
            title=config.mesh.name,
            project=project,
        )
    except Exception as exc:
        logger.error("Viewer failed to start: %s", exc)


async def run_local_client(config: AgentMeshConfig) -> None:
    """Run a mesh client watcher so the server also submits its own sessions."""
    from synix.mesh.client import MeshClient
    from synix.mesh.config import load_mesh_config, resolve_mesh_root

    config_path = resolve_mesh_root() / config.mesh.name / "synix-mesh.toml"
    mesh_config = load_mesh_config(config_path)

    # Small delay to let the server start first
    await asyncio.sleep(3)

    client = MeshClient(mesh_config)
    logger.info("Local client watcher started (submitting own sessions to localhost:%s)", config.mesh.port)
    await client.start()


async def serve(
    config: AgentMeshConfig,
    *,
    viewer: bool = True,
    mcp: bool = True,
    mcp_port: int = MCP_DEFAULT_PORT,
) -> None:
    """Start mesh server, MCP HTTP server, local client watcher, and optionally viewer.

    - Mesh server: async uvicorn on mesh port (7433)
    - MCP HTTP server: async uvicorn on MCP port (8200)
    - Local client: watches + submits this machine's own sessions
    - Viewer: threaded Flask on viewer port (9471)
    """
    loop = asyncio.get_event_loop()

    tasks = []

    # Mesh server (async)
    tasks.append(asyncio.create_task(run_mesh_server(config)))

    # MCP HTTP server (async)
    if mcp:
        tasks.append(asyncio.create_task(run_mcp_http(config, port=mcp_port)))

    # Local client watcher (submits this machine's sessions)
    tasks.append(asyncio.create_task(run_local_client(config)))

    # Viewer (threaded Flask)
    if viewer:
        loop.run_in_executor(None, run_viewer, config)

    # Handle shutdown
    stop = asyncio.Event()

    def _signal_handler():
        logger.info("Shutting down...")
        stop.set()
        for t in tasks:
            t.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    import contextlib

    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks)

# synix-agent-mesh

Cross-device agent memory mesh. One CLI to capture, build, search, and serve your AI conversation history across multiple machines.

Built on [synix](https://pypi.org/project/synix/) (build system for agent memory) and synix mesh (cross-device session sync).

## Quick Start

```bash
pip install synix-agent-mesh
# or
uv tool install synix-agent-mesh
```

Initialize a new project:

```bash
sam init --name my-memory
cd my-memory
```

Start the server (mesh + MCP + viewer):

```bash
sam serve
```

This starts three services in one process:

| Service     | Port | Description                                      |
|-------------|------|--------------------------------------------------|
| Mesh server | 7433 | Session ingest, builds, artifact distribution    |
| MCP HTTP    | 8200 | 20 synix tools over Streamable HTTP              |
| Viewer      | 9471 | Web UI for browsing and searching memory          |

## Architecture

```
                    ┌─────────────────────────────────┐
                    │         Salinas (server)         │
                    │                                  │
                    │  sam serve                        │
                    │  ├── mesh server    :7433         │
                    │  ├── MCP HTTP       :8200         │
                    │  ├── local client   (watcher)     │
                    │  └── viewer         :9471         │
                    └──────┬───────────┬───────────────┘
                           │           │
              ┌────────────┘           └────────────┐
              ▼                                     ▼
    ┌──────────────────┐                  ┌──────────────────┐
    │  Oxnard (router) │                  │ Obispo (client)  │
    │                  │                  │                  │
    │  MCP router      │                  │  sam join         │
    │  └─ memory_*     │◄─proxy──────────►│  ├── watcher     │
    │     (20 tools)   │   :8200          │  ├── puller      │
    └──────────────────┘                  │  └── heartbeat   │
                                          └──────────────────┘
```

**Server node** runs `sam serve` — the mesh server accepts session submissions from clients, triggers builds, and serves artifacts. The MCP HTTP endpoint exposes all synix tools over the network. The local client watcher submits the server's own Claude Code sessions.

**Client nodes** run `sam join` — they watch for new Claude Code sessions, submit them to the server, and pull build artifacts back.

**MCP router nodes** proxy the synix MCP tools through an aggregated MCP endpoint (e.g., alongside email, browser, todoist tools).

## Installation

### From PyPI

```bash
pip install synix-agent-mesh
```

### From source (development)

```bash
git clone https://github.com/marklubin/synix-agent-mesh.git
cd synix-agent-mesh
uv sync
```

For local development with editable synix:

```bash
# Clone synix alongside synix-agent-mesh
git clone https://github.com/marklubin/synix.git ../synix

# uv.sources in pyproject.toml will use local editable install
uv sync
```

## Configuration

All configuration lives in `agent-mesh.toml` at your project root.

### Full reference

```toml
# --- Mesh networking ---
[mesh]
name = "agent-memory"         # Mesh identifier (used for ~/.synix-mesh/<name>/)
port = 7433                   # Mesh server port

# --- Web viewer ---
[viewer]
port = 9471                   # Viewer HTTP port
host = "0.0.0.0"              # Bind address ("127.0.0.1" for local-only)

# --- Data sources ---
# Each [sources.<name>] block defines a directory to ingest.

[sources.sessions]
dir = "~/.claude/projects"           # Directory to scan
patterns = ["**/*.jsonl"]            # Glob patterns to match
description = "Live Claude Code sessions"

[sources.exports]
dir = "./sources"
description = "Static ChatGPT/Claude exports"

[sources.notes]
dir = "~/notes"
patterns = ["**/*.md"]
description = "Personal notes"
enabled = false                      # Disabled sources are skipped

# --- LLM provider ---
# Used for episode summarization, rollups, and synthesis.
[llm]
provider = "openai-compatible"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
temperature = 0.3
max_tokens = 8192

# --- Pipeline feature toggles ---
[pipeline]
weekly_rollup = true           # Generate weekly activity summaries
work_status = true             # Generate rolling work status report
context_budget = 10000         # Max tokens for core synthesis context

# --- Deploy hooks ---
# Shell commands to run after build completes.
[deploy]
server_commands = []           # Run on server after build
client_commands = []           # Run on client after artifact pull
```

### Environment variable overrides

LLM settings can be overridden with environment variables (useful for secrets):

| Variable           | Overrides        |
|--------------------|------------------|
| `SAM_LLM_PROVIDER` | `llm.provider`  |
| `SAM_LLM_BASE_URL` | `llm.base_url`  |
| `SAM_LLM_MODEL`    | `llm.model`     |

Set your API key in the environment used by your LLM provider. For OpenAI-compatible providers, `OPENAI_API_KEY` is typically expected.

## CLI Reference

### `sam init`

Scaffold a new agent-mesh project.

```bash
sam init                          # Initialize in current directory
sam init --dir ~/my-mesh          # Initialize in specific directory
sam init --name custom-mesh       # Use custom mesh name
```

Creates:
- `agent-mesh.toml` with default configuration
- `sources/` directory for static exports
- `pipeline.py` stub
- Synix project (`.synix/` directory)
- Mesh instance at `~/.synix-mesh/<name>/`

### `sam serve`

Start the mesh server, MCP HTTP server, local client watcher, and viewer.

```bash
sam serve                         # Start everything
sam serve --no-viewer             # Skip viewer (headless server)
sam serve --no-mcp                # Skip MCP HTTP server
sam serve --mcp-port 9000         # Custom MCP port
```

All services run in a single process. Use Ctrl+C or send SIGTERM for graceful shutdown.

**Running as a systemd service:**

```ini
# ~/.config/systemd/user/sam-serve.service
[Unit]
Description=synix-agent-mesh server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/your/project
ExecStart=/path/to/uv run sam serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now sam-serve.service
```

### `sam join`

Join an existing mesh as a client node.

```bash
sam join --server myhost:7433
sam join --server myhost:7433 --name custom-mesh
```

The client daemon:
1. **Watches** `~/.claude/projects` for new/updated session files
2. **Submits** sessions to the mesh server
3. **Pulls** build artifacts from the server
4. **Heartbeats** to maintain cluster membership

Before joining, ensure:
- The mesh exists locally: `sam init --name <same-name-as-server>`
- The mesh token matches the server's token (copy from server's `~/.synix-mesh/<name>/synix-mesh.toml`)

**Running as a systemd service:**

```ini
# ~/.config/systemd/user/sam-client.service
[Unit]
Description=synix-agent-mesh client
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/your/project
ExecStart=/path/to/uv run sam join --server myhost:7433
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

### `sam build`

Trigger a pipeline build.

```bash
sam build                         # Remote build (via mesh server)
sam build --local                 # Local build (skip mesh)
sam build --local --force         # Force rebuild
sam build --local -v              # Verbose output
```

The build pipeline processes sources through layers:

```
Sources → Episodes → Monthly/Weekly Rollups → Core Synthesis → Work Status
```

Each layer produces searchable artifacts with full provenance tracking.

### `sam search`

Search agent memory.

```bash
sam search "kubernetes deployment"
sam search "memory architecture" --mode semantic
sam search "bug fix" --mode hybrid --limit 5
sam search "project status" --release local
```

| Option      | Values                           | Default   |
|-------------|----------------------------------|-----------|
| `--mode`    | `keyword`, `semantic`, `hybrid`  | `keyword` |
| `--limit`   | 1–100                            | 10        |
| `--release` | Release name                     | `local`   |

### `sam status`

Show mesh health, members, and build status.

```bash
sam status
```

Output includes:
- Mesh role (server/client) and hostname
- Connected members
- Source directories and their status
- Build count and pending sessions

### `sam sources`

Manage data sources.

```bash
sam sources list                  # Show all configured sources
sam sources add notes ~/notes --patterns "**/*.md" --description "Personal notes"
sam sources disable notes         # Set enabled = false
```

### `sam view`

Start the viewer standalone (without the mesh server).

```bash
sam view                          # View the 'local' release
sam view --release my-release     # View a specific release
```

### `sam mcp-config`

Print MCP server configuration for Claude Code.

```bash
sam mcp-config
```

## Multi-Machine Setup

### Step 1: Server node

On your primary machine:

```bash
sam init --name agent-memory
sam serve
```

Note the mesh token from `~/.synix-mesh/agent-memory/synix-mesh.toml`.

### Step 2: Client nodes

On each additional machine:

```bash
# Install
pip install synix-agent-mesh

# Initialize with the same mesh name
sam init --name agent-memory

# Copy the server's mesh token
# Edit ~/.synix-mesh/agent-memory/synix-mesh.toml
# Replace the token value with the server's token

# Join the mesh
sam join --server <server-hostname>:7433
```

### Step 3: MCP access

**Local (stdio) — on the server machine:**

Add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "agent-mesh": {
      "command": "uv",
      "args": ["--directory", "/path/to/project", "run", "python", "-m", "synix.mcp"],
      "env": {"SYNIX_PROJECT": "/path/to/project"}
    }
  }
}
```

**Remote (HTTP) — on any machine that can reach the server:**

```json
{
  "mcpServers": {
    "agent-mesh": {
      "type": "streamable-http",
      "url": "http://<server-hostname>:8200/mcp"
    }
  }
}
```

**Via MCP router (aggregated with other tools):**

Create a proxy backend in your MCP router:

```python
# backends/memory.py
import os
from fastmcp import FastMCP

MCP_URL = os.environ.get('AGENT_MESH_MCP_URL', 'http://server:8200/mcp')
mcp = FastMCP.as_proxy(MCP_URL)
```

Mount it in the router:

```python
from backends import memory
router.mount(memory.mcp, prefix='memory')
```

This exposes all 20 synix tools with a `memory_` prefix (e.g., `memory_search`, `memory_get_artifact`).

### Token management

Each mesh has a shared secret token. All nodes must use the same token to communicate.

```bash
# View the token
grep '^token' ~/.synix-mesh/agent-memory/synix-mesh.toml

# Copy to a client machine
scp server:~/.synix-mesh/agent-memory/synix-mesh.toml /tmp/mesh-token.txt
# Then paste the token line into the client's synix-mesh.toml
```

## MCP Tools

When the MCP HTTP server is running, these 20 tools are available:

| Tool | Description |
|------|-------------|
| `search` | Search the memory index (keyword, semantic, hybrid) |
| `get_artifact` | Retrieve a specific artifact by label |
| `get_flat_file` | Get a projected flat file (context.md, etc.) |
| `list_artifacts` | List artifacts in a layer |
| `list_layers` | List all pipeline layers |
| `list_releases` | List available releases |
| `show_release` | Show release metadata |
| `lineage` | Trace provenance chain for an artifact |
| `list_refs` | List artifact references |
| `open_project` | Open a synix project |
| `init_project` | Initialize a new synix project |
| `load_pipeline` | Load a pipeline definition |
| `build` | Trigger a build |
| `release` | Create a release |
| `source_add_text` | Add text content as a source |
| `source_add_file` | Add a file as a source |
| `source_list` | List current sources |
| `source_remove` | Remove a source |
| `source_clear` | Clear all sources |
| `clean` | Clean build artifacts |

## Pipeline

The default pipeline processes conversation data through hierarchical layers:

```
Layer 0: Raw Sources
  └── Exports, live sessions, transcripts

Layer 1: Episodes
  └── Each conversation summarized into a structured episode

Layer 2: Rollups
  ├── Monthly — grouped by calendar month
  └── Weekly — last 8 weeks of activity summaries

Layer 3: Synthesis
  ├── Core — distilled context document from all episodes
  └── Work Status — rolling status report (projects, blockers, upcoming)
```

Each layer's output feeds into the next. All artifacts include full provenance tracking back to the original source conversations.

### Custom transforms

The pipeline uses synix transforms. To add custom processing, edit `pipeline.py` in your project:

```python
from synix_agent_mesh.config import load_config
from synix_agent_mesh.pipeline import build_pipeline

config = load_config()
pipeline = build_pipeline(config)

# Add custom transforms, projections, etc.
# pipeline.add_transform(...)
```

## Viewer

The viewer is included with synix (via the `synix[viewer]` extra, pulled in automatically).

It provides:

- Layer-by-layer artifact browsing
- Full-text search across all memory
- Provenance chain visualization
- Artifact detail view with metadata

Access at `http://<host>:9471` when running `sam serve` or `sam view`.

The viewer serves data from synix releases. Run `sam build --local` to create a release, then `sam view` to browse it. When running `sam serve`, the viewer automatically picks the first available release.

## Versioning

synix-agent-mesh shadows synix's version number. Both packages share the same version (e.g., `0.20.2`). This keeps compatibility obvious: agent-mesh `0.20.x` works with synix `0.20.x`.

## Development

```bash
git clone https://github.com/marklubin/synix-agent-mesh.git
cd synix-agent-mesh
uv sync
uv run pytest tests/ -v
uv run ruff check .
```

### Project layout

```
src/synix_agent_mesh/
├── __init__.py        # Version
├── cli.py             # Click CLI (sam command)
├── config.py          # TOML config loading + dataclasses
├── server.py          # Async orchestrator (mesh + MCP + viewer + client)
└── pipeline.py        # Pipeline builder + transforms (WeeklyRollup, WorkStatus)
```

## License

MIT

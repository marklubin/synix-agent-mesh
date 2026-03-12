# sam — persistent memory for coding agents

Every coding agent has the same problem: it forgets.

Context compaction silently destroys hours of accumulated understanding. Every new session starts from zero. Decisions you made yesterday? Gone. Work from your laptop? Your desktop agent has no idea.

`sam` fixes this. It watches your sessions, builds structured memory, and gives your agent searchable access to everything via MCP. When your agent starts a new conversation, it already knows what you've been working on.

Setup takes 60 seconds. It runs in the background. You never think about it.

## Quick Start

```bash
uv tool install synix-agent-mesh
sam setup
sam serve
```

That's it. `sam setup` detects your coding agents, configures MCP, and injects behavioral instructions so your agent proactively uses memory — no manual JSON editing, no CLAUDE.md maintenance.

### Verify

```bash
sam doctor
```

Checks project health, sources, build state, search index, MCP connectivity, and LLM access. Reports issues with fix commands.

## What You Get

After setup, your agent automatically:

- **Remembers past conversations.** "We discussed the auth refactor last week" becomes something your agent can actually recall, not something you have to re-explain.

- **Knows your active projects.** Work status, blockers, recent decisions — synthesized from your sessions, updated with every build.

- **Searches across everything.** Keyword, semantic, or hybrid search across all your past conversations. Every result links back to the original session.

- **Works across machines.** Your laptop and desktop share one memory. Work on machine A, pick up on machine B.

### Before sam vs. after sam

| Without | With |
|---------|------|
| "Let me re-explain what we decided yesterday" | Agent recalls the decision and its rationale |
| Context compaction silently destroys hours of work | Every conversation is indexed and searchable |
| Laptop work invisible to desktop agent | Shared memory across all machines |
| Manual CLAUDE.md maintenance | Automatic context generation and behavioral integration |
| Generic agent, every session | Agent that knows your projects, preferences, patterns |

## Works With

| Agent | Auto-Setup | Transport | Behavioral Integration |
|-------|-----------|-----------|----------------------|
| **Claude Code** | `sam setup` configures | streamable-http | Appends to `~/.claude/CLAUDE.md` |
| **Cursor** | `sam setup` configures | http | Creates `~/.cursor/rules/sam-memory.mdc` |
| **Windsurf** | `sam setup` configures | streamable-http | Appends to `~/.codeium/windsurf/.windsurfrules` |
| **Roo Code** | `sam setup` configures | streamable-http | Appends to `~/.roo/rules.md` |
| **VS Code + Copilot** | Manual | http | — |

`sam setup` does two things for each detected agent:
1. **MCP configuration** — adds the `memory` server to the agent's MCP config
2. **Behavioral integration** — injects instructions that tell the agent to proactively search memory at session start

Both are idempotent — safe to run multiple times. Never overwrites existing content.

<details>
<summary>Manual MCP configuration (VS Code, Cline, etc.)</summary>

Add this to your agent's MCP config:

```json
{
  "mcpServers": {
    "memory": {
      "type": "streamable-http",
      "url": "http://localhost:8200/mcp"
    }
  }
}
```

Use `"type": "http"` for Cursor and VS Code, `"type": "sse"` for Cline.

</details>

## Agent Installation Guide

If you're an AI agent helping a user set up sam, follow these steps.

### Prerequisites

- Python 3.11+ (`python3 --version`)
- `uv` installed (`which uv` — install via `curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Steps

1. Install sam:
   ```bash
   uv tool install synix-agent-mesh
   ```

2. Run setup (detects agents, configures MCP + behavioral rules):
   ```bash
   sam setup
   ```
   This is safe to run — never overwrites existing configuration. Only appends new entries. Use `--dry-run` first if uncertain.

3. Start the memory server:
   ```bash
   sam serve
   ```
   Runs in the foreground. For background operation, set up a systemd user service (see below).

4. Verify:
   ```bash
   sam doctor
   ```

### Autonomy classification

| Step | Level | Reversible | Notes |
|------|-------|------------|-------|
| `uv tool install` | Safe — proceed | Yes (`uv tool uninstall synix-agent-mesh`) | Standard package install |
| `sam setup --dry-run` | Safe — proceed | Read-only | Shows what would be configured |
| `sam setup` | Confirm first time | Yes (manual revert) | Writes to agent config files |
| `sam serve` | Confirm | Yes (Ctrl+C) | Starts network services on :7433 and :8200 |
| `sam doctor` | Safe — proceed | Read-only | Inspection only |

## Adding Sources

`sam` watches directories you configure. Claude Code sessions are enabled by default.

```bash
# Add personal notes
sam sources add notes ~/notes --patterns "**/*.md" --description "Personal notes"

# Add ChatGPT exports
sam sources add chatgpt ~/Downloads/chatgpt-exports --description "ChatGPT export files"

# List all sources
sam sources list
```

Or edit `agent-mesh.toml` directly:

```toml
[sources.notes]
dir = "~/notes"
patterns = ["**/*.md"]
description = "Personal notes and documents"
```

## Multiple Machines

Run `sam serve` on your primary machine. Join from others:

```bash
# On each additional machine:
uv tool install synix-agent-mesh
sam setup --name agent-memory
# Copy the mesh token from your server's ~/.synix-mesh/agent-memory/synix-mesh.toml
sam join --server your-server:7433
```

Sessions from every machine feed into one shared memory. Every machine pulls the latest build artifacts.

Point any agent at the server's MCP endpoint: `http://your-server:8200/mcp`

## CLI Reference

| Command | What it does |
|---------|-------------|
| `sam setup` | Detect agents, configure MCP + behavioral rules, initialize project |
| `sam doctor` | Health check — project, sources, build, search, MCP, LLM |
| `sam serve` | Start mesh server (:7433), MCP server (:8200), session watcher |
| `sam join --server HOST:PORT` | Join an existing mesh as a client node |
| `sam build --local` | Trigger a pipeline build locally |
| `sam search "query"` | Search memory from the command line |
| `sam status` | Mesh health, connected members, build status |
| `sam sources list` | Show configured data sources |
| `sam sources add NAME DIR` | Add a new data source |

### `sam setup`

| Flag | Default | Effect |
|------|---------|--------|
| `--name NAME` | agent-memory | Mesh name |
| `--dir PATH` | current dir | Project directory |
| `--no-mcp` | — | Skip MCP + behavioral configuration |
| `--agent AGENT` | all detected | Configure only this agent |
| `--mcp-url URL` | `http://localhost:8200/mcp` | Custom MCP endpoint |
| `--dry-run` | — | Show what would be configured without writing |

### `sam doctor`

| Flag | Effect |
|------|--------|
| `--json` | Machine-readable JSON output |
| `--check CATEGORY` | Run one category: project, sources, build, search, mcp, llm |
| `--no-llm-test` | Skip LLM connectivity test |

### `sam serve`

| Flag | Default | Effect |
|------|---------|--------|
| `--no-mcp` | MCP on | Skip the MCP HTTP server |
| `--mcp-port PORT` | 8200 | Custom MCP server port |

### `sam search`

| Flag | Values | Default |
|------|--------|---------|
| `--mode` | `keyword`, `semantic`, `hybrid` | `keyword` |
| `--limit` | 1–100 | 10 |
| `--release` | release name | `local` |

## MCP Tools

When `sam serve` is running, 20 tools are available to any connected agent:

### Read tools (safe for autonomous use)

| Tool | Description |
|------|-------------|
| `search` | Search memory — keyword, semantic, or hybrid |
| `get_artifact` | Retrieve a specific artifact by label |
| `get_flat_file` | Get a projected document (context-doc, work-status-doc) |
| `list_artifacts` | List artifacts in a pipeline layer |
| `list_layers` | List all pipeline layers |
| `list_releases` | List available releases |
| `show_release` | Show release metadata and stats |
| `lineage` | Trace provenance chain for any artifact |
| `list_refs` | List artifact cross-references |
| `open_project` | Open a synix project by path |
| `source_list` | List configured sources |

### Write tools (confirm on first use)

| Tool | Description |
|------|-------------|
| `init_project` | Initialize a new synix project |
| `load_pipeline` | Load a pipeline definition |
| `build` | Trigger a pipeline build |
| `release` | Create a named release snapshot |
| `source_add_text` | Add text content as a source |
| `source_add_file` | Add a file as a source |
| `source_remove` | Remove a source |

### Destructive tools (always confirm)

| Tool | Description |
|------|-------------|
| `clean` | Delete all build artifacts — irreversible |
| `source_clear` | Clear all sources — irreversible |

## Configuration

All settings live in `agent-mesh.toml` at your project root. Created by `sam setup`.

<details>
<summary>Full configuration reference</summary>

```toml
# --- Mesh networking ---
[mesh]
name = "agent-memory"         # Mesh identifier
port = 7433                   # Mesh server port

# --- Data sources ---
[sources.sessions]
dir = "~/.claude/projects"
patterns = ["**/*.jsonl"]
description = "Live Claude Code sessions"

[sources.exports]
dir = "./sources"
description = "Static ChatGPT/Claude exports"

# --- LLM provider ---
[llm]
provider = "openai-compatible"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
temperature = 0.3
max_tokens = 8192

# --- Pipeline ---
[pipeline]
weekly_rollup = true
work_status = true
context_budget = 10000

# --- Sync ---
[sync]
auto_build = true             # Build when new sessions arrive
build_cooldown = 300           # Seconds between auto-builds
build_threshold = 3            # Sessions required to trigger

# --- Deploy hooks ---
[deploy]
server_commands = []
client_commands = []
```

</details>

### Environment variables

| Variable | Overrides |
|----------|-----------|
| `OPENAI_API_KEY` | API key for the configured LLM provider |
| `SAM_LLM_PROVIDER` | `llm.provider` |
| `SAM_LLM_BASE_URL` | `llm.base_url` |
| `SAM_LLM_MODEL` | `llm.model` |

## How It Works

### The pipeline

| Layer | What | Output |
|-------|------|--------|
| 0 | Raw sources | Session transcripts, exports, notes |
| 1 | Episodes | Each conversation → structured summary |
| 2 | Rollups | Episodes → weekly and monthly summaries |
| 3 | Synthesis | Rollups → context document + work status report |

Each layer feeds the next. All outputs include provenance metadata. Builds are incremental — only new sessions get processed.

### The mesh

- **Server** (`sam serve`): accepts sessions, runs builds, serves MCP tools and artifacts
- **Clients** (`sam join`): watch for local sessions, submit to server, pull artifacts back
- Nodes authenticate with a shared mesh token

<details>
<summary>Running as a systemd service</summary>

**Server:**
```ini
# ~/.config/systemd/user/sam-serve.service
[Unit]
Description=sam memory server
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/project
ExecStart=/path/to/uv run sam serve
Restart=on-failure

[Install]
WantedBy=default.target
```

**Client:**
```ini
# ~/.config/systemd/user/sam-client.service
[Unit]
Description=sam memory client
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/project
ExecStart=/path/to/uv run sam join --server your-server:7433
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now sam-serve.service
```

</details>

## Development

```bash
git clone https://github.com/marklubin/synix-agent-mesh.git
cd synix-agent-mesh
uv sync
uv run pytest tests/ -v
```

## License

MIT

---

<sub>Powered by [synix](https://pypi.org/project/synix/) — the build system for agent memory.</sub>

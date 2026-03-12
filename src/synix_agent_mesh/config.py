"""Configuration loading and validation for synix-agent-mesh."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = "agent-mesh.toml"


@dataclass
class SourceConfig:
    """A single data source."""

    name: str
    dir: str
    patterns: list[str] = field(default_factory=lambda: ["**/*"])
    description: str = ""
    enabled: bool = True

    @property
    def resolved_dir(self) -> Path:
        return Path(self.dir).expanduser().resolve()


@dataclass
class MeshConfig:
    """Mesh networking settings."""

    name: str = "agent-memory"
    port: int = 7433


@dataclass
class ViewerConfig:
    """Viewer settings."""

    port: int = 9471
    host: str = "127.0.0.1"


@dataclass
class LLMConfig:
    """LLM provider settings."""

    provider: str = "openai-compatible"
    base_url: str = "https://api.kimi.com/coding/v1"
    model: str = "kimi-for-coding"
    temperature: float = 0.3
    max_tokens: int = 8192

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


@dataclass
class PipelineConfig:
    """Pipeline feature toggles."""

    weekly_rollup: bool = True
    work_status: bool = True
    context_budget: int = 10000


@dataclass
class AutoBuildConfig:
    """Auto-build settings for sam serve."""

    enabled: bool = True
    cooldown: int = 300  # seconds between builds
    scan_interval: int = 60  # seconds between source scans


@dataclass
class DeployConfig:
    """Deploy hook settings."""

    server_commands: list[str] = field(default_factory=list)
    client_commands: list[str] = field(default_factory=list)


@dataclass
class AgentMeshConfig:
    """Top-level configuration."""

    project_dir: Path
    mesh: MeshConfig = field(default_factory=MeshConfig)
    viewer: ViewerConfig = field(default_factory=ViewerConfig)
    sources: list[SourceConfig] = field(default_factory=list)
    llm: LLMConfig = field(default_factory=LLMConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    auto_build: AutoBuildConfig = field(default_factory=AutoBuildConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)


def load_config(project_dir: Path | None = None) -> AgentMeshConfig:
    """Load configuration from agent-mesh.toml in the given directory.

    Falls back to cwd if project_dir is None.
    Applies env var overrides for secrets.
    """
    if project_dir is None:
        project_dir = Path.cwd()
    project_dir = project_dir.resolve()

    config_path = project_dir / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {project_dir}. "
            f"Run 'sam init' to create one."
        )

    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    return _parse_config(raw, project_dir)


def _parse_config(raw: dict, project_dir: Path) -> AgentMeshConfig:
    """Parse raw TOML dict into AgentMeshConfig."""
    # Mesh
    mesh_raw = raw.get("mesh", {})
    mesh = MeshConfig(
        name=mesh_raw.get("name", "agent-memory"),
        port=mesh_raw.get("port", 7433),
    )

    # Viewer
    viewer_raw = raw.get("viewer", {})
    viewer = ViewerConfig(
        port=viewer_raw.get("port", 9471),
        host=viewer_raw.get("host", "127.0.0.1"),
    )

    # Sources
    sources = []
    for name, src_raw in raw.get("sources", {}).items():
        sources.append(SourceConfig(
            name=name,
            dir=src_raw.get("dir", f"./{name}"),
            patterns=src_raw.get("patterns", ["**/*"]),
            description=src_raw.get("description", ""),
            enabled=src_raw.get("enabled", True),
        ))

    # LLM — env vars override config for secrets
    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        provider=os.environ.get("SAM_LLM_PROVIDER", llm_raw.get("provider", "openai-compatible")),
        base_url=os.environ.get("SAM_LLM_BASE_URL", llm_raw.get("base_url", "https://api.kimi.com/coding/v1")),
        model=os.environ.get("SAM_LLM_MODEL", llm_raw.get("model", "kimi-for-coding")),
        temperature=float(llm_raw.get("temperature", 0.3)),
        max_tokens=int(llm_raw.get("max_tokens", 8192)),
    )

    # Pipeline
    pipeline_raw = raw.get("pipeline", {})
    pipeline = PipelineConfig(
        weekly_rollup=pipeline_raw.get("weekly_rollup", True),
        work_status=pipeline_raw.get("work_status", True),
        context_budget=pipeline_raw.get("context_budget", 10000),
    )

    # Auto-build
    auto_build_raw = raw.get("auto_build", {})
    auto_build = AutoBuildConfig(
        enabled=auto_build_raw.get("enabled", True),
        cooldown=int(auto_build_raw.get("cooldown", 300)),
        scan_interval=int(auto_build_raw.get("scan_interval", 60)),
    )

    # Deploy
    deploy_raw = raw.get("deploy", {})
    deploy = DeployConfig(
        server_commands=deploy_raw.get("server_commands", []),
        client_commands=deploy_raw.get("client_commands", []),
    )

    return AgentMeshConfig(
        project_dir=project_dir,
        mesh=mesh,
        viewer=viewer,
        sources=sources,
        llm=llm,
        pipeline=pipeline,
        auto_build=auto_build,
        deploy=deploy,
    )

"""Pipeline stub — real pipeline built dynamically from agent-mesh.toml.

This file exists so synix mesh can reference a pipeline path.
The actual pipeline is constructed by synix_agent_mesh.pipeline.build_pipeline().
"""

from synix_agent_mesh.config import load_config
from synix_agent_mesh.pipeline import build_pipeline

config = load_config()
pipeline = build_pipeline(config)

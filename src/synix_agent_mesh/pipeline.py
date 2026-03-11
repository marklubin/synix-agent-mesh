"""Default pipeline template for synix-agent-mesh.

Generates a synix Pipeline from agent-mesh.toml configuration.
Ported from unified-memory's pipeline.py + transforms.py.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from synix import FlatFile, Pipeline, SearchSurface, Source, SynixSearch
from synix.core.models import Artifact, Transform
from synix.transforms import CoreSynthesis, EpisodeSummary, MonthlyRollup

from synix_agent_mesh.config import AgentMeshConfig

# ---------------------------------------------------------------------------
# LLM helper — calls the configured provider directly
# ---------------------------------------------------------------------------

def _llm_complete(config: AgentMeshConfig, messages: list[dict], desc: str, max_tokens: int | None = None) -> str:
    """Call the configured LLM provider. Returns content string."""
    import openai

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set — needed for LLM transforms")

    client = openai.OpenAI(
        api_key=api_key,
        base_url=config.llm.base_url,
        default_headers={"User-Agent": "synix-agent-mesh/0.1"},
    )

    tok = max_tokens or config.llm.max_tokens
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=config.llm.model,
                messages=messages,
                max_tokens=tok,
                temperature=config.llm.temperature,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                reasoning = getattr(response.choices[0].message, "reasoning_content", None)
                if reasoning:
                    print(
                        f"[sam] Warning: empty content for {desc}, "
                        f"reasoning used {len(reasoning)} chars.",
                        file=sys.stderr,
                    )
            return content
        except (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError) as exc:
            if attempt == 0:
                import time
                print(f"[sam] Transient error for {desc}, retrying in 5s: {exc}", file=sys.stderr)
                time.sleep(5)
            else:
                raise RuntimeError(f"Failed {desc} after 2 attempts: {exc}") from exc
        except openai.APIError as exc:
            raise RuntimeError(f"LLM API error for {desc}: {exc}") from exc

    raise RuntimeError(f"Failed {desc}")


# ---------------------------------------------------------------------------
# Custom transforms
# ---------------------------------------------------------------------------

# Module-level config holder — set by build_pipeline() before transforms run
_active_config: AgentMeshConfig | None = None


def _get_model_config() -> dict:
    """Return LLM config dict for artifact metadata (no secrets)."""
    if _active_config is None:
        return {}
    return _active_config.llm.to_dict()


class WeeklyRollup(Transform):
    """Group episodes by ISO week, synthesize each. Only last 8 weeks."""

    prompt_name = None

    PROMPT = """\
You are synthesizing conversation summaries from ISO week {week} ({week_start} to {week_end}) into a status update.

<episode_summaries>
{episodes}
</episode_summaries>

Write a weekly rollup (200-400 words) that captures:
1. What was worked on this week — projects, tasks, investigations
2. Key accomplishments and milestones reached
3. Important decisions made and their rationale
4. Open items, blockers, or things to follow up on
5. Recurring patterns or themes

Write in a factual, status-report style. Use present/past tense. Be specific about project names and outcomes."""

    def _week_cutoff(self) -> str:
        cutoff = datetime.now() - timedelta(weeks=8)
        iso = cutoff.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    def _week_range(self, week_key: str) -> tuple[str, str]:
        year, week_str = week_key.split("-W")
        monday = datetime.strptime(f"{year}-W{week_str}-1", "%G-W%V-%u")
        sunday = monday + timedelta(days=6)
        return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")

    def _episode_week_key(self, ep: Artifact) -> str | None:
        date_str = ep.metadata.get("date", "")
        if not date_str or len(date_str) < 10:
            return None
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
            iso = dt.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        except ValueError:
            return None

    def split(self, inputs: list[Artifact], config: dict) -> list[tuple[list[Artifact], dict]]:
        cutoff = self._week_cutoff()
        weeks: dict[str, list[Artifact]] = defaultdict(list)

        for ep in inputs:
            week_key = self._episode_week_key(ep)
            if week_key is None or week_key < cutoff:
                continue
            weeks[week_key].append(ep)

        if not weeks:
            print("[sam] Warning: no episodes in last 8 weeks for WeeklyRollup", file=sys.stderr)
            return []

        return [
            (episodes, {"_week_key": week})
            for week, episodes in sorted(weeks.items())
        ]

    def estimate_output_count(self, input_count: int) -> int:
        return min(8, max(input_count // 5, 1))

    def get_cache_key(self, config: dict) -> str:
        cutoff = self._week_cutoff()
        return hashlib.sha256(cutoff.encode()).hexdigest()[:8]

    def compute_fingerprint(self, config: dict):
        from synix.build.fingerprint import Fingerprint, compute_digest, fingerprint_value

        components: dict[str, str] = {}
        components["transform_id"] = fingerprint_value(f"{type(self).__module__}.{type(self).__qualname__}")
        try:
            components["source"] = fingerprint_value(inspect.getsource(type(self)))
        except (OSError, TypeError):
            components["source"] = fingerprint_value(type(self).__qualname__)
        components["config"] = fingerprint_value(self.get_cache_key(config))
        components["model"] = fingerprint_value(_get_model_config())
        return Fingerprint(
            scheme="synix:transform:v2",
            digest=compute_digest(components),
            components=components,
        )

    def execute(self, inputs: list[Artifact], config: dict) -> list[Artifact]:
        week_key = config.get("_week_key")
        if week_key is None:
            results: list[Artifact] = []
            for unit_inputs, config_extras in self.split(inputs, config):
                merged = {**config, **config_extras}
                results.extend(self.execute(unit_inputs, merged))
            return results

        week_start, week_end = self._week_range(week_key)
        sorted_inputs = sorted(inputs, key=lambda ep: ep.artifact_id)
        episodes_text = "\n\n---\n\n".join(
            f"### {ep.metadata.get('title', ep.label)} ({ep.metadata.get('date', '')})\n{ep.content}"
            for ep in sorted_inputs
        )

        prompt = self.PROMPT.format(
            week=week_key, week_start=week_start, week_end=week_end, episodes=episodes_text,
        )

        content = _llm_complete(
            _active_config,
            messages=[{"role": "user", "content": prompt}],
            desc=f"weekly rollup {week_key}",
        )

        return [
            Artifact(
                label=f"weekly-{week_key}",
                artifact_type="rollup",
                content=content,
                input_ids=[ep.artifact_id for ep in inputs],
                model_config=_get_model_config(),
                metadata={
                    "week": week_key,
                    "week_start": week_start,
                    "week_end": week_end,
                    "episode_count": len(inputs),
                },
            )
        ]


class WorkStatusReport(Transform):
    """Synthesize last 4 weekly rollups into structured work status report."""

    prompt_name = None

    PROMPT = """\
You are generating a structured work status report from the last 4 weeks of weekly rollups.

<weekly_rollups>
{rollups}
</weekly_rollups>

Generate a structured work status report with these sections:

## Active Projects
For each active project, include:
- **Status**: (active / stalled / wrapping up)
- **Recent work**: What happened in the last 1-4 weeks
- **Next steps**: What's planned or likely next

## Recently Completed
Projects or significant tasks completed in the last 4 weeks.

## Upcoming / Planned
Work that's been discussed or planned but not yet started.

## Blockers & Open Questions
Unresolved issues, decisions needed, or things waiting on external input.

Be specific about project names, tools, and outcomes. Write concisely. \
Focus on actionable information. Skip generic observations."""

    def _week_cutoff(self) -> str:
        cutoff = datetime.now() - timedelta(weeks=4)
        iso = cutoff.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    def split(self, inputs: list[Artifact], config: dict) -> list[tuple[list[Artifact], dict]]:
        cutoff = self._week_cutoff()
        recent = [inp for inp in inputs if inp.metadata.get("week", "") >= cutoff]
        if not recent:
            recent = sorted(inputs, key=lambda a: a.metadata.get("week", ""))[-4:]
        return [(recent, {})]

    def estimate_output_count(self, input_count: int) -> int:
        return 1

    def get_cache_key(self, config: dict) -> str:
        cutoff = self._week_cutoff()
        return hashlib.sha256(cutoff.encode()).hexdigest()[:8]

    def compute_fingerprint(self, config: dict):
        from synix.build.fingerprint import Fingerprint, compute_digest, fingerprint_value

        components: dict[str, str] = {}
        components["transform_id"] = fingerprint_value(f"{type(self).__module__}.{type(self).__qualname__}")
        try:
            components["source"] = fingerprint_value(inspect.getsource(type(self)))
        except (OSError, TypeError):
            components["source"] = fingerprint_value(type(self).__qualname__)
        components["config"] = fingerprint_value(self.get_cache_key(config))
        components["model"] = fingerprint_value(_get_model_config())
        return Fingerprint(
            scheme="synix:transform:v2",
            digest=compute_digest(components),
            components=components,
        )

    def execute(self, inputs: list[Artifact], config: dict) -> list[Artifact]:
        if not inputs:
            return [
                Artifact(
                    label="work-status",
                    artifact_type="report",
                    content="# Work Status Report\n\nNo weekly rollups available yet.",
                    metadata={"week_count": 0},
                )
            ]

        sorted_inputs = sorted(inputs, key=lambda r: r.metadata.get("week", ""))
        rollups_text = "\n\n---\n\n".join(
            f"### Week {r.metadata.get('week', r.label)} "
            f"({r.metadata.get('week_start', '?')} to {r.metadata.get('week_end', '?')})\n{r.content}"
            for r in sorted_inputs
        )

        prompt = self.PROMPT.format(rollups=rollups_text)

        content = _llm_complete(
            _active_config,
            messages=[{"role": "user", "content": prompt}],
            desc="work status report",
            max_tokens=4096,
        )

        weeks = [r.metadata.get("week", "") for r in sorted_inputs]
        return [
            Artifact(
                label="work-status",
                artifact_type="report",
                content=content,
                input_ids=[r.artifact_id for r in inputs],
                model_config=_get_model_config(),
                metadata={
                    "week_count": len(inputs),
                    "weeks_covered": weeks,
                },
            )
        ]


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------

def build_pipeline(config: AgentMeshConfig) -> Pipeline:
    """Build a synix Pipeline from AgentMeshConfig.

    Sets the module-level _active_config so transforms can access LLM settings.
    """
    global _active_config
    _active_config = config

    pipeline = Pipeline("agent-mesh")
    pipeline.llm_config = {
        **config.llm.to_dict(),
        "default_headers": {"User-Agent": "synix-agent-mesh/0.1"},
    }

    # --- Sources ---
    source_layers = []
    for src in config.sources:
        if not src.enabled:
            continue
        source = Source(src.name, dir=str(src.resolved_dir))
        source_layers.append(source)

    if not source_layers:
        raise ValueError("No enabled sources in configuration")

    # --- Layer 1: Episodes ---
    episodes = EpisodeSummary("episodes", depends_on=source_layers)

    # --- Layer 2a: Monthly rollups ---
    monthly = MonthlyRollup("monthly", depends_on=[episodes])

    layers = [*source_layers, episodes, monthly]

    # --- Layer 2b: Weekly rollups (optional) ---
    weekly = None
    if config.pipeline.weekly_rollup:
        weekly = WeeklyRollup("weekly", depends_on=[episodes])
        layers.append(weekly)

    # --- Layer 3: Core synthesis ---
    core = CoreSynthesis("core", depends_on=[monthly], context_budget=config.pipeline.context_budget)
    layers.append(core)

    # --- Layer 4: Work status (optional, requires weekly) ---
    work_status = None
    if config.pipeline.work_status and weekly is not None:
        work_status = WorkStatusReport("work-status", depends_on=[weekly])
        layers.append(work_status)

    pipeline.add(*layers)

    # --- Projections ---
    searchable = [episodes, monthly, core]
    if weekly:
        searchable.append(weekly)

    memory_surface = SearchSurface(
        "memory-surface",
        sources=searchable,
        modes=["fulltext", "semantic"],
        embedding_config={
            "provider": "fastembed",
            "model": "BAAI/bge-small-en-v1.5",
        },
    )
    memory_search = SynixSearch("memory-search", surface=memory_surface)

    projections = [memory_surface, memory_search]

    context_doc = FlatFile("context-doc", sources=[core])
    projections.append(context_doc)

    if weekly:
        projections.append(FlatFile("weekly-status-doc", sources=[weekly]))

    if work_status:
        projections.append(FlatFile("work-status-doc", sources=[work_status]))

    pipeline.add(*projections)

    return pipeline

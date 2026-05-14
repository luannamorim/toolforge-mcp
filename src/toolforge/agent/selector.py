"""Tool-server selector implementing SPEC § Tool selection heuristic.

Five rules run in strict priority order; first match wins. The selected server,
the rule that fired, and the alternatives considered are returned so the
orchestrator can record them in every trace.

Rule strings (written to TraceRecord.selection_rule):
  "single-candidate"   — only one server exposes this tool; shortcut, no heuristic.
  "explicit-mention"   — rule 1: user named the server in the prompt.
  "argument-type"      — rule 2: only one candidate schema validates the LLM args.
  "session-recency"    — rule 3: most recently used server for an overlapping tool.
  "cosine-similarity"  — rule 4: tool description vs prompt embedding wins by margin.
  "priority-order"     — rule 5: first by mcp.servers.json order (final tiebreaker).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import jsonschema

from toolforge.models.catalog import ToolDescriptor

COSINE_MARGIN_DEFAULT = 0.05
RULE_NO_CANDIDATE = "no-candidate"


@dataclass
class SelectionContext:
    prompt: str
    session_used_servers: list[str] = field(default_factory=list)
    priority_order: list[str] = field(default_factory=list)
    prompt_embedding: list[float] | None = None
    cosine_margin: float = COSINE_MARGIN_DEFAULT


def select_server(
    tool_name: str,
    candidates: list[ToolDescriptor],
    ctx: SelectionContext,
    tool_input: dict | None = None,
) -> tuple[ToolDescriptor, str, list[str]]:
    """Return (selected_tool, rule_that_fired, alternative_server_ids).

    Raises ValueError when candidates is empty.
    """
    if not candidates:
        raise ValueError(f"No candidates for tool '{tool_name}'")

    if len(candidates) == 1:
        return candidates[0], "single-candidate", []

    for rule_fn in (
        lambda: _rule_explicit_mention(candidates, ctx),
        lambda: _rule_argument_type_match(candidates, tool_input),
        lambda: _rule_session_recency(candidates, ctx),
        lambda: _rule_cosine_similarity(candidates, ctx),
        lambda: _rule_priority_order(candidates, ctx),
    ):
        result = rule_fn()
        if result is not None:
            return result

    # rule 5 always returns a result for non-empty candidates; never reached
    raise RuntimeError("BUG: no selection rule fired")  # pragma: no cover


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _rule_explicit_mention(
    candidates: list[ToolDescriptor],
    ctx: SelectionContext,
) -> tuple[ToolDescriptor, str, list[str]] | None:
    """Rule 1: user explicitly names exactly one candidate server in the prompt."""
    prompt_tokens = set(re.findall(r"\b\w+\b", ctx.prompt.lower()))
    matching = [c for c in candidates if c.server_id.lower() in prompt_tokens]
    if len(matching) != 1:
        return None
    selected = matching[0]
    alternatives = [c.server_id for c in candidates if c is not selected]
    return selected, "explicit-mention", alternatives


def _rule_argument_type_match(
    candidates: list[ToolDescriptor],
    tool_input: dict | None,
) -> tuple[ToolDescriptor, str, list[str]] | None:
    """Rule 2: exactly one candidate schema validates the LLM-produced arguments."""
    if tool_input is None:
        return None
    valid = []
    for c in candidates:
        try:
            jsonschema.validate(tool_input, c.input_schema)
            valid.append(c)
        except (jsonschema.ValidationError, jsonschema.SchemaError):
            pass
    if len(valid) != 1:
        return None
    selected = valid[0]
    alternatives = [c.server_id for c in candidates if c is not selected]
    return selected, "argument-type", alternatives


def _rule_session_recency(
    candidates: list[ToolDescriptor],
    ctx: SelectionContext,
) -> tuple[ToolDescriptor, str, list[str]] | None:
    """Rule 3: most recently used server among candidates wins."""
    if not ctx.session_used_servers:
        return None
    candidate_ids = {c.server_id for c in candidates}
    # Walk from most recent to oldest; first match wins
    for server_id in reversed(ctx.session_used_servers):
        if server_id in candidate_ids:
            selected = next(c for c in candidates if c.server_id == server_id)
            alternatives = [c.server_id for c in candidates if c is not selected]
            return selected, "session-recency", alternatives
    return None


def _rule_cosine_similarity(
    candidates: list[ToolDescriptor],
    ctx: SelectionContext,
) -> tuple[ToolDescriptor, str, list[str]] | None:
    """Rule 4: highest cosine(prompt_embedding, description_embedding) by margin."""
    if ctx.prompt_embedding is None:
        return None
    scored = [
        (_dot(ctx.prompt_embedding, c.description_embedding), c)
        for c in candidates
        if c.description_embedding is not None
    ]
    # Require ≥2 scored candidates — one score is not a comparison.
    if len(scored) < 2:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, selected = scored[0]
    if top_score - scored[1][0] < ctx.cosine_margin:
        return None  # too close to call
    alternatives = [c.server_id for _, c in scored[1:]]
    return selected, "cosine-similarity", alternatives


def _rule_priority_order(
    candidates: list[ToolDescriptor],
    ctx: SelectionContext,
) -> tuple[ToolDescriptor, str, list[str]]:
    """Rule 5: first by mcp.servers.json priority order (final tiebreaker)."""
    def rank(t: ToolDescriptor) -> int:
        try:
            return ctx.priority_order.index(t.server_id)
        except ValueError:
            return len(ctx.priority_order)

    ordered = sorted(candidates, key=rank)
    selected = ordered[0]
    alternatives = [c.server_id for c in ordered[1:]]
    return selected, "priority-order", alternatives


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

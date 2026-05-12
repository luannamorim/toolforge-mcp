"""Tool-server selector.

Phase 1: single-candidate shortcut — returns the only candidate and fires rule
"single-candidate". Interface is stable; Phase 2 implements the full 5-rule
heuristic (SPEC § Tool selection heuristic) without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from toolforge.models.catalog import ToolDescriptor


@dataclass
class SelectionContext:
    prompt: str
    session_used_servers: list[str] = field(default_factory=list)


def select_server(
    tool_name: str,
    candidates: list[ToolDescriptor],
    ctx: SelectionContext,
) -> tuple[ToolDescriptor, str]:
    """Return (selected_tool, rule_that_fired).

    Raises ValueError when candidates is empty.
    Raises NotImplementedError for multi-candidate selection (Phase 2+).
    """
    if not candidates:
        raise ValueError(f"No candidates for tool '{tool_name}'")

    if len(candidates) == 1:
        return candidates[0], "single-candidate"

    # Phase 2: implement 5-rule heuristic here.
    raise NotImplementedError(
        f"Multi-server selection not yet implemented for tool '{tool_name}' "
        f"(candidates: {[c.server_id for c in candidates]})"
    )

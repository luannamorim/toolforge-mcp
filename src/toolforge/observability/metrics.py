from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("toolforge")

_task_latency = _meter.create_histogram(
    "toolforge.task.latency_ms",
    unit="ms",
    description="End-to-end latency of one /chat request",
)
_task_cost = _meter.create_histogram(
    "toolforge.task.cost_usd",
    unit="USD",
    description="Anthropic API cost for one /chat request",
)
_tool_errors = _meter.create_counter(
    "toolforge.tool.errors_total",
    description="Number of failed tool calls",
)
_selection_rule = _meter.create_counter(
    "toolforge.selection.heuristic_rule_fired",
    description="Number of times each tool-selection rule fired",
)


def record_task(
    latency_ms: float,
    cost_usd: float,
    *,
    halted: bool,
    halt_reason: str | None,
) -> None:
    attrs: dict[str, str | bool] = {"halted": halted}
    if halt_reason is not None:
        attrs["halt_reason"] = halt_reason
    _task_latency.record(latency_ms, attributes=attrs)
    _task_cost.record(cost_usd, attributes=attrs)


def record_tool_error(server: str, tool: str, reason: str) -> None:
    _tool_errors.add(1, attributes={"server": server, "tool": tool, "reason": reason})


def record_selection_rule(rule: str, server: str) -> None:
    _selection_rule.add(1, attributes={"rule": rule, "server": server})

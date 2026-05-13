from __future__ import annotations

import hashlib
import json
from pathlib import Path

from toolforge.models.trace import TraceRecord

# Sonnet 4.6 pricing (USD per million tokens, 2025)
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
        "cache_write": 1.00,
    },
}
_DEFAULT_PRICING = _PRICING["claude-sonnet-4-6"]


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    p = _PRICING.get(model, _DEFAULT_PRICING)
    regular_in = max(0, input_tokens - cache_read_tokens - cache_creation_tokens)
    cost = (
        regular_in * p["input"]
        + cache_read_tokens * p["cache_read"]
        + cache_creation_tokens * p["cache_write"]
        + output_tokens * p["output"]
    ) / 1_000_000
    return round(cost, 8)


def hash_arguments(arguments: dict) -> str:
    serialized = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


class TraceWriter:
    def __init__(self, sink: Path, verbose: bool = False) -> None:
        self._sink = sink
        self._verbose = verbose
        sink.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: TraceRecord) -> dict:
        data = record.model_dump(exclude_none=True)
        if not self._verbose:
            data.pop("arguments", None)
        with self._sink.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
        return data

from datetime import UTC, datetime

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"


class TraceRecord(BaseModel):
    schema_version: str = SCHEMA_VERSION
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    session_id: str
    step: int
    server: str
    tool: str
    arguments_hash: str
    latency_ms: float
    success: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float
    selection_rule: str
    executed: bool = True
    error: str | None = None
    alternatives: list[str] | None = None  # server_ids considered but not selected
    arguments: dict | None = None  # only when TRACE_VERBOSE=1

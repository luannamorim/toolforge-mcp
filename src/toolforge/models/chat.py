import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[dict] = Field(
        default_factory=list,
        description=(
            "Optional prior conversation turns supplied by the caller. The "
            "server is stateless: it does not persist or look up history by "
            "session_id. Pass the full prior turn list on each request to "
            "preserve context across calls."
        ),
    )
    dry_run: bool = False


class ChatResponse(BaseModel):
    session_id: str
    response: str
    steps: int
    cost_usd: float
    dry_run: bool = False
    halted: bool = False
    halt_reason: Literal["cost_ceiling"] | None = None

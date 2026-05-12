import uuid

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[dict] = Field(default_factory=list)
    dry_run: bool = False


class ChatResponse(BaseModel):
    session_id: str
    response: str
    steps: int
    cost_usd: float
    dry_run: bool = False

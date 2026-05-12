from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class ServerStatus(BaseModel):
    id: str
    connected: bool


class HealthResponse(BaseModel):
    status: str
    servers: list[ServerStatus]


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    pool = request.app.state.pool
    servers = [
        ServerStatus(id=sid, connected=ok)
        for sid, ok in pool.connection_status.items()
    ]
    all_ok = all(s.connected for s in servers)
    return HealthResponse(status="ok" if all_ok else "degraded", servers=servers)

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class ServerStatus(BaseModel):
    id: str
    connected: bool


class CacheStatus(BaseModel):
    connected: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    servers: list[ServerStatus]
    cache: CacheStatus


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    pool = request.app.state.pool
    cache = request.app.state.cache
    servers = [
        ServerStatus(id=sid, connected=ok)
        for sid, ok in pool.connection_status.items()
    ]
    cache_ok = await cache.ping()
    all_ok = all(s.connected for s in servers) and cache_ok
    return HealthResponse(
        status="ok" if all_ok else "degraded",
        servers=servers,
        cache=CacheStatus(connected=cache_ok),
    )

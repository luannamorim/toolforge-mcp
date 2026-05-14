from __future__ import annotations

from anthropic import RateLimitError
from fastapi import FastAPI
from fastapi.responses import JSONResponse

_DEFAULT_RETRY_AFTER = "60"


def retry_after(exc: RateLimitError) -> str:
    if exc.response is not None:
        return exc.response.headers.get("retry-after", _DEFAULT_RETRY_AFTER)
    return _DEFAULT_RETRY_AFTER


def register_rate_limit_handler(app: FastAPI) -> None:
    @app.exception_handler(RateLimitError)
    async def _handler(_request, exc: RateLimitError) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": "Anthropic API rate limit exceeded; retry after the indicated delay"},
            headers={"Retry-After": retry_after(exc)},
        )

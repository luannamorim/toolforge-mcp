from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class PayloadSizeMiddleware(BaseHTTPMiddleware):
    """Reject POST /chat* requests whose body exceeds max_bytes (SPEC.md:137).

    Checks Content-Length before reading the body. When Content-Length is
    absent (chunked transfer), the request passes through — the SPEC cap
    applies to declared payloads. Requests to other paths pass unchanged.
    """

    def __init__(self, app, max_bytes: int = 32 * 1024) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path.startswith("/chat"):
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > self._max_bytes:
                        return JSONResponse({"detail": "payload too large"}, status_code=400)
                except ValueError:
                    return JSONResponse({"detail": "invalid content-length"}, status_code=400)
        return await call_next(request)

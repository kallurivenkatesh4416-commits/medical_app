"""Consistent error envelope (brief §10): {"error": {code, message, details}}.

Stack traces are never leaked to clients.
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code=f"http_{exc.status_code}", message=str(exc.detail)),
    )


async def unhandled_exception_handler(_: Request, __: Exception) -> JSONResponse:
    # Never leak the exception to the client; details stay in server logs.
    return JSONResponse(
        status_code=500,
        content=error_body(code="internal_error", message="An unexpected error occurred."),
    )

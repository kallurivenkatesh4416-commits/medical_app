"""Health endpoints (brief §3 backend, §9 step 1).

- /healthz : liveness — process is up (no dependencies).
- /readyz  : readiness — DB reachable; 503 otherwise.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db import check_database
from app.errors import error_body

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> JSONResponse:
    if check_database():
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(
        status_code=503,
        content=error_body(code="db_unavailable", message="Database not reachable."),
    )

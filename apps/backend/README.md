# Backend — FastAPI

Residential Emergency Health Response Platform API.

- Stack: FastAPI, SQLModel/SQLAlchemy 2.x, Alembic, Postgres 15+, structlog.
- Provider gateways (`NotificationGateway`, `StorageGateway`) selected by
  `PROVIDER_MODE=stub|live` — see `../../PLAN.md`.

## Dev

```bash
python -m venv .venv
. .venv/Scripts/activate           # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
pytest
ruff check .
```

Health: `GET /healthz` (liveness), `GET /readyz` (readiness — DB reachable).

_Layout (`app/`, `alembic/`, `tests/`) lands in commit 3._

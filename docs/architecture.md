# Architecture

> Living document — updated every slice. Authoritative scope/decisions: `../PLAN.md`.

## Components (target)

- **apps/backend** — FastAPI (Python 3.11+), SQLModel/SQLAlchemy 2.x, Alembic.
  Owns all APIs, RBAC, audit log, notification fan-out, PDF generation.
- **apps/dashboard** — React (Vite) + TS + TanStack Query + shadcn/ui (doctor +
  admin; nurse uses doctor dashboard with reduced permissions for MVP).
- **apps/mobile** — Flutter resident app.
- **packages/shared-types** — canonical enums + OpenAPI-generated TS types.

## Provider abstraction

`NotificationGateway` (push/SMS/voice) and `StorageGateway` (S3) selected by
`PROVIDER_MODE=stub|live`. Business logic depends only on the interfaces. Fan-out
fires push + SMS + voice in parallel; each logs independently to
`notification_attempts`; one failing never blocks the others.

## Emergency data flow

To be diagrammed in Slice 5–6 (button → idempotent API → `emergency_case` →
parallel fan-out → dashboard live feed → lifecycle → handover PDF).

_Status: skeleton (Slice 1). Filled per slice._

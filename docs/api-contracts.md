# API Contracts

> Living document — updated every slice.

- OpenAPI spec served by the backend at `/openapi.json` (Swagger UI at `/docs`).
- `packages/shared-types` is generated from this spec; CI checks for drift.

## Conventions

- **Auth:** OTP login → JWT short-lived access + refresh. RBAC enforced server-side.
- **Idempotency:** all resource-creating writes accept an `Idempotency-Key` header.
- **Pagination:** every list endpoint paginated; cursor pagination for live feeds.
- **Error envelope:** `{ "error": { "code", "message", "details" } }`. No stack
  traces to clients.

## Endpoints

| Method | Path | Slice | Notes |
|---|---|---|---|
| GET | `/healthz` | 1 | liveness |
| GET | `/readyz` | 1 | readiness (DB reachable) |

_Status: skeleton (Slice 1). Endpoints added per slice._

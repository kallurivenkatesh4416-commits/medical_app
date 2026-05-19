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
| POST | `/api/v1/auth/otp/request` | 2 | sends OTP (stub SMS in dev); `dev_otp` only when `APP_ENV=local` |
| POST | `/api/v1/auth/otp/verify` | 2 | verifies OTP → access + refresh tokens |
| POST | `/api/v1/auth/refresh` | 2 | rotates refresh (single-use, reuse-detected) |
| POST | `/api/v1/auth/logout` | 2 | revokes the refresh token (204) |
| GET | `/api/v1/auth/me` | 2 | current user (Bearer access token) |

Auth: `Authorization: Bearer <access>`. Access tokens are short-lived JWTs;
refresh tokens are opaque, stored hashed, rotated on every use.

_Endpoints added per slice._

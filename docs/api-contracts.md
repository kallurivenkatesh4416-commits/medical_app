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
| POST | `/api/v1/auth/otp/verify` | 3 | now returns either a token pair OR `registration_required` + `registration_token` |
| GET | `/api/v1/projects` | 3 | project lookup (names only; registration token) |
| POST | `/api/v1/onboarding/complete` | 3 | atomic resident self-registration → token pair; honours `Idempotency-Key` (retry → working session, not 409) |
| GET | `/api/v1/me/profile` | 3 | resident's own profile (PHI; audited) |
| GET | `/api/v1/me/consents` | 3 | resident's consents |
| PATCH | `/api/v1/me/consents/{consent_type}` | 3 | grant/revoke; `data_storage`=false closes account |
| GET | `/api/v1/residents/{id}/profile` | 3 | staff view (doctor/nurse/ops; PHI-role-blocked; consent-gated; audited) |
| POST | `/api/v1/me/records` | 4 | resident upload (multipart PDF/JPEG/PNG, tags, `Idempotency-Key`; audited) |
| GET | `/api/v1/me/records` | 4 | resident list of own medical records (audited) |
| GET | `/api/v1/me/records/{id}/link` | 4 | resident signed download link; TTL hard-capped at 900s |
| GET | `/api/v1/residents/{resident_id}/records` | 4 | staff list (doctor/nurse/ops; PHI-blocked, tenant-isolated, consent-gated, audited) |
| GET | `/api/v1/residents/{resident_id}/records/{id}/link` | 4 | staff signed download link; same guards as staff list |
| GET | `/api/v1/records/download/{token}` | 4 | local stub signed-link download; invalid/tampered/expired tokens return 401 |

Auth: `Authorization: Bearer <access>`. Access tokens are short-lived JWTs;
refresh tokens are opaque, stored hashed, rotated on every use.

_Endpoints added per slice._

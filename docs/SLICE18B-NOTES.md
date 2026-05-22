# Slice 18b Notes - Dashboard Cookie Auth and CSRF

> Handoff for the dashboard session-transport follow-up to Slice 18.

## What landed

### Backend

- Dashboard OTP verify is explicit: `dashboard_session=true` returns no
  browser-readable token pair and writes HttpOnly access, refresh, and stable
  dashboard-session cookies instead.
- Mobile and compatibility clients keep the bearer token JSON contract on the
  same OTP, refresh, logout, and RBAC services.
- Access-cookie authentication is accepted by the existing `get_current_user`
  dependency only when no bearer header is supplied.
- Cookie-authenticated `POST`, `PATCH`, and `DELETE` requests require the
  signed per-session `X-CSRF-Token`. Bearer-only requests do not.
- `GET /api/v1/auth/csrf` issues the in-memory dashboard CSRF token;
  cookie refresh and logout verify it before rotating or revoking refresh
  tokens.
- Dashboard CORS now enables credentials only for the exact origins listed in
  `DASHBOARD_ORIGINS`.

### Dashboard

- No refresh token remains in `sessionStorage`; no access token is kept in
  React or browser storage.
- `ApiClient` includes credentials on all API calls, probes `/auth/csrf` at
  bootstrap, retries one 401 via cookie refresh, and attaches CSRF to write
  calls.
- Staff login requests the dashboard cookie transport while role routing,
  binary export fetches, the alert workflow, and logout keep the Slice 15
  component boundaries.

## Verification

| Surface | Result |
|---|---|
| Backend `ruff` | clean |
| Backend SQLite `pytest` | 203 passed, 2 Postgres-only tests skipped |
| Backend `alembic check` | clean |
| Dashboard typecheck/lint/build | clean |
| Dashboard Vitest | 5 passed; cookie bootstrap and handover CSRF header covered |
| Mobile analyze/test | clean; Flutter 73 passed |
| `git diff --check` | clean; Git emitted existing LF-to-CRLF working-copy warnings |

# Slice 18b Brief - Dashboard Cookie Auth and CSRF

> Slice 18 hardens JWT startup, OTP abuse controls, uploads, and Postgres
> verification. This follow-up isolates the dashboard session migration so it
> can be reviewed as an auth change instead of being buried in a mixed slice.

## Goal

Move the staff dashboard off browser-readable refresh-token storage while
preserving the mobile bearer-token flow and existing RBAC boundaries.

## Scope

### Backend

- Add dashboard-focused HttpOnly access and refresh cookies with secure
  production defaults.
- Keep bearer-token auth available for mobile and the compatibility rollout.
- Make refresh and logout work with cookie sessions without weakening refresh
  reuse detection.
- Add CSRF issue and verification for cookie-authenticated mutating requests.
- Flip dashboard CORS to exact-origin credentialed requests only when cookie
  endpoints are in place.

### Dashboard

- Remove refresh token persistence in `sessionStorage`.
- Switch API calls to credentialed fetches.
- Bootstrap from the cookie session and fetch CSRF state before mutating
  requests.
- Attach the CSRF header on POST, PATCH, and DELETE requests.
- Keep role routing, 401 handling, binary export download behavior, and the
  staff workflow from Slice 15 intact.

## Default design

- Cookie names and `Path` values are explicit and scoped to auth/API routes.
- Production cookies use `HttpOnly`, `Secure`, and strict SameSite defaults.
- CSRF tokens are bound to the authenticated dashboard session and are not
  accepted on bearer-only mobile requests.
- The rollout accepts bearer and cookie sessions together first; disabling
  dashboard bearer refresh is a later release switch after browser testing.

## Tests

- Backend tests for cookie issue, refresh, logout, CSRF rejection, CSRF success,
  refresh reuse detection, and exact-origin CORS.
- Dashboard Vitest coverage for cookie bootstrap, CSRF header attachment,
  expired-session routing, and the handover mutation path.
- No mobile changes beyond regression verification.

## Out of scope

- SSO and staff identity-provider integration.
- Production TLS/WAF provisioning.
- Any change to resident mobile secure-storage tokens.

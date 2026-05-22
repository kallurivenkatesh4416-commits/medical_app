# Slice 15 Notes — Dashboard rebuild

> Purpose: capture the Slice 15 surface area, the test cadence, and
> the integration points future slices should honour. Same shape as
> `SLICE11-HANDOFF.md` / `SLICE14-NOTES.md` / `SLICE16-NOTES.md` /
> `SLICE17-NOTES.md`.

## Branch state

- Slice 15 lands on `slice-6-emergency-hardening` (same working base
  as Slices 14, 16, 16-review-#1, 17). `main` untouched; merge plan
  in `docs/SLICE13-MERGE-PLAN.md`.

## What this slice delivers

Closes audit P1 #4 ("Dashboard auth/session model is unsafe and not
yet the doctor product in the brief"). The token-paste console is
gone. The dashboard is the brief's "Doctor + Admin" surface: live
emergency feed → case workflow → patient context → handover; with
the builder-admin KPI surface untouched-PHI-wise.

### Stack changes

| Layer | Before | After |
|---|---|---|
| Routing | none — single component | `react-router-dom` v6 with role-guarded routes |
| Data | hand-rolled `fetch` + `setInterval` | TanStack Query v5 (polling at 5s on the alert feed + case detail) |
| Styles | inline `React.CSSProperties` | Tailwind v3 + shadcn/ui token system (own-the-source) |
| Auth | bearer-token-paste UI, token in `localStorage` | real OTP login, in-memory access + `sessionStorage` refresh, silent re-auth on reload |
| Tests | none | Vitest + RTL + jsdom — auth gate, alert→case→handover flow, builder-admin no-PHI nav |

### File layout

```
apps/dashboard/src/
├── api/
│   ├── auth.ts           OTP / me / refresh / logout typed wrappers
│   ├── client.ts         ApiClient — in-mem access + sessionStorage refresh
│   └── endpoints.ts      emergency, staff (profile/records/medicines), handover, admin
├── auth/
│   ├── AuthContext.tsx   single owner of client + user + status
│   └── RouteGuard.tsx    role-aware wrapper with /login + /403 routing
├── components/
│   ├── AppShell.tsx      header + role-conditional nav + footer
│   ├── Footer.tsx        DISCLAIMER_SHORT (mirrors mobile safety.dart)
│   └── ui/               button, input, card, badge (shadcn-style, inlined)
├── pages/
│   ├── LoginPage.tsx
│   ├── AlertsListPage.tsx
│   ├── CaseDetailPage.tsx      lifecycle + vitals + notes + handover panel
│   ├── PatientProfilePage.tsx  profile + history + records + medicines + contacts
│   ├── AdminKpisPage.tsx
│   ├── AdminExportsPage.tsx    binary download via ApiClient.requestRaw
│   └── ForbiddenPage.tsx
├── safety.ts             canonical DISCLAIMER_SHORT + empty-state copy
├── lib/utils.ts          shadcn `cn()` helper
├── index.css             Tailwind + shadcn HSL token theme
└── test/                 Vitest+RTL test suite (helpers, auth, case-flow, admin-no-phi)
```

### Auth storage decision (Slice 15 scoping)

User picked **Path 1** (in-memory access + sessionStorage refresh):

- Access token: held only in the `ApiClient` instance (module-scoped
  inside `AuthProvider`). Never touches disk. Lost on tab close.
- Refresh token: `sessionStorage` under
  `auth.refresh_token.v1`. Per-tab, cleared on tab close.
- On boot, `AuthContext` calls `client.tryRestoreSession()` →
  silent `POST /auth/refresh`. If it succeeds, `GET /auth/me`
  populates the user; otherwise routes to `/login`. While the
  bootstrap is in flight, `RouteGuard` renders `<SplashLoader/>` so
  we never paint a "logged out" UI for a valid session.
- Mid-session refresh failures (Slice 2 reuse detection,
  server-side revoke) fire `ApiClient.onSessionExpired` → user
  state clears → next route render bounces to `/login`.

This is "zero-backend-changes" per the slice scope. The HttpOnly
cookie path (Option B) is parked for the auth-hardening pass.

### Backend prereq (narrowly scoped)

Only one thing required: `CORSMiddleware`. Without it, the browser
blocks every fetch from `http://localhost:5173` to `http://localhost:8000`.

- `app/config.py` adds `dashboard_origins` (comma-separated
  allowlist; default `http://localhost:5173`).
- `app/main.py` mounts `CORSMiddleware` when the list is non-empty.
- `.env.example` documents `DASHBOARD_ORIGINS`.
- `allow_credentials=False` because Slice 15 uses bearer tokens, not
  cookies. The cookie-auth migration (audit P1 hardening) will flip
  this alongside CSRF tokens.

No other backend changes — every endpoint the dashboard calls
already exists.

## What this slice deliberately does NOT do

Out of scope per the slice ask:

- **AWS deploy.** Same as Slice 14/16/17 — punted to the
  deployment-hardening pass.
- **Backend auth-perimeter P1 hardening.** JWT_SECRET validation,
  OTP rate limits, login lockout, cookie auth + CSRF — all
  remain in the hardening pass. Slice 15 stayed inside the
  in-memory token strategy specifically to avoid pre-empting that.
- **Upload MIME sniffing.** Records still trust caller-declared
  content type (audit P1 #5).
- **Postgres-CI / load proof.** Slice 17 documented this remains
  a `[HARDENING]` item.
- **Dashboard-side e2e against a live backend.** The Vitest suite
  exercises the routing + render contract against fake HTTP; a
  full Playwright sweep against `docker compose up` is a separate
  task.

## Test cadence (Slice 15 baseline)

`npm run typecheck` clean. `npm run lint` clean. `npm run build`
clean (267 kB JS / 13 kB CSS gzipped to 84 / 3.5 kB).
`npm run test` (Vitest):

| Suite | Coverage |
|---|---|
| `test/auth.test.tsx` (3 tests) | Unauthenticated → `/login` with `?next=` preserved; authenticated doctor → alert feed via silent refresh; builder_admin hitting `/alerts` → `/403` page |
| `test/case-flow.test.tsx` (1 test) | Alert feed → case detail click-through → handover generate (asserts POST body shape + signed-link anchor) |
| `test/admin-no-phi.test.tsx` (1 test) | KPI page renders aggregate numbers only; nav does NOT advertise `/alerts` to the admin role; canonical PHI-shaped strings stay out of the DOM |

The HTTP layer is faked by `src/test/helpers.tsx` — a tiny route
matcher injected as `fetch` into `ApiClient`. Tests never touch the
network and the `sessionStorage` path is exercised against the
`InMemoryStorage` fallback.

## Integration points the next slice should honour

### 1. The dashboard is now the canonical staff UI

The token-paste UI in the old `App.tsx` is removed. Any backend
slice that adds a new doctor-facing endpoint should also add the
typed wrapper to `src/api/endpoints.ts` and the corresponding page
hook — not surface it as "paste a curl into the console".

### 2. Role-gating happens in two places

- Frontend `RouteGuard` keeps a builder_admin out of `/alerts`
  cleanly (no 403 round-trip per click).
- Backend `forbid_phi_roles` is the actual security boundary —
  removing the route guard would not change the security posture;
  it would just produce a worse UX (403 modals instead of nav
  redirects).

A new role added to `app/enums.py::Role` MUST be mirrored in
`apps/dashboard/src/api/auth.ts::Role` and either added to
`PHI_ROLES` / `ADMIN_ROLES` or left out so the RouteGuard
fail-closes.

### 3. The brief §2.1 disclaimer footer is on every staff page

Same canonical string as the mobile (`DISCLAIMER_SHORT`). The
wording history in `docs/ux-copy.md` is real — paraphrasing
reintroduced the word "diagnose" once and was reverted. Import
the constant from `src/safety.ts`, don't inline the text.

### 4. Binary downloads use `ApiClient.requestRaw`

The admin export page is the only Slice 15 surface that returns
binary (CSV / PDF). It uses `ApiClient.requestRaw` so the bearer
token + 401-refresh + `sessionExpired` handling are consistent
with every JSON page. A future binary endpoint (e.g. record blob
download outside the signed-link path) should reuse the same
helper.

### 5. ApiClient's `setSession` is the only token entry point

Any future auth path (mobile-app-spawned dashboard via deep link,
SSO callback, etc.) must funnel its credentials through
`client.setSession({ access, refresh })`. The access token field
on `ApiClient` is private specifically so no caller can
inadvertently leak it to `localStorage`.

### 6. TanStack Query keys are project-stable

The alert feed and case detail use keys
`["alerts", "active"]` and `["alerts", "detail", caseId]`. The
case-detail lifecycle mutation invalidates the entire `["alerts"]`
subtree so the list refreshes at the next tick.

## Verification baseline (carry forward)

| Suite | Result |
|---|---|
| Backend `ruff` | All checks passed |
| Backend `pytest` | 186 passed (Slice 17 baseline) — unchanged this slice |
| `shared-types` typecheck | Clean (no changes this slice) |
| Dashboard `typecheck` | Clean |
| Dashboard `lint` | Clean (max-warnings 0) |
| Dashboard `build` | Clean — 267 kB JS / 13 kB CSS gzipped to 84 / 3.5 kB |
| Dashboard `test` (Vitest) | **5 passed** |
| `flutter analyze` | Clean (no mobile work this slice) |
| `flutter test` | 73 passed (unchanged) |

## Next-slice ordering

Slice 15 closes the audit P1 #4 finding ("dashboard remains a slice
demo console, not a safe production doctor workspace"). Remaining
audit items:

- **P1 #3** Auth perimeter (JWT secret validation, OTP rate limits,
  cookie auth + CSRF) — hardening pass.
- **P1 #5** Upload MIME sniffing + virus scan — hardening pass.
- **P2 #6** Postgres-CI + load/p95 evidence — hardening pass.
- **P2 #7** CORS / proxy story — closed by Slice 15's `CORSMiddleware`
  (the audit's specific call-out is now satisfied; tighter cookie-auth
  CORS comes with the P1 #3 cookie work).

The hardening pass is the natural next slice. Order suggestion:

1. JWT_SECRET non-default validation at startup (cheap, high signal).
2. OTP request rate limit per phone + per IP (closes SMS-bomb).
3. Upload MIME sniffing (`python-magic` or libmagic) + size already
   in place.
4. Postgres-CI: add a Postgres service to `.github/workflows/ci.yml`
   gated by an env var and run the refresh-rotation concurrency test
   there.
5. Cookie auth + CSRF + dashboard re-wiring to use it.

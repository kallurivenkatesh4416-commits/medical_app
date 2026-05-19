# Open Questions — Operational

> Operational / business unknowns hit during implementation. Tagged
> `[NEEDS_OPS_DECISION]`. Implementation continues with a sensible **technical
> default** (recorded here) and never invents business policy. Legal questions go to
> `docs/compliance-notes.md` under `[NEEDS_LEGAL_REVIEW]` instead.
>
> The "Builder Pilot Operating Plan" (doctor staffing, night/weekend coverage, SLA
> promises, ambulance contracts, pricing, resident onboarding ops, patient-worsens
> liability) is the user's **separate business document** — not in this repo, not
> stubbed, not committed.

## Items

_None yet. Format below._

### `[NEEDS_OPS_DECISION]` <short title>
- **Question:** …
- **Why it matters:** …
- **Technical default applied:** … (so build continues)
- **Surfaced in:** Slice N / file

## Technical hardening backlog (non-blocking, `[HARDENING]`)

### `[HARDENING]` Postgres-backed concurrency test for refresh rotation
- **What:** `rotate_refresh` relies on `SELECT ... FOR UPDATE` row locking. The
  lock path only exists on Postgres; local/CI tests run on SQLite (which
  ignores `FOR UPDATE`), so the true multi-process race is not exercised.
- **Plan:** add a Postgres service to CI and a concurrency test that fires two
  simultaneous refreshes and asserts exactly one survives and replay revokes
  the family. Fits the Slice 12 hardening pass.
- **Surfaced in:** Slice 2 review (auth_service.rotate_refresh).

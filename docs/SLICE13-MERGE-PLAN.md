# Slice 13 — Merge / PR plan for Slices 1–12

> Purpose: lightweight branch-hygiene checkpoint. Slices 1–12 currently live
> on `slice-6-emergency-hardening` (origin tip `cdcd39a`); `main` is still
> empty. This plan stages the existing work for review **without merging
> ahead of Slice 14** — Slice 14 keeps `slice-6-emergency-hardening` as its
> working base.
>
> Cadence per `PLAN.md` ("implement → verify → commit → stop for review")
> is unchanged. This doc just defines the merge order and what each PR claims.

## Branch state at Slice 13 start

```
slice-6-emergency-hardening @ cdcd39a (12 slices, 4 commits ahead of origin)
main                        @ (untouched — never received any of the work)
```

Local has 4 unpushed commits: Slice 11 implementation, Slice 11 review fix,
Slice 12 hardening, and Slice 12 hardening report. Push these to origin
*before* opening any PR so the review tip matches what reviewers can fetch.

```powershell
git push -u origin slice-6-emergency-hardening
```

## Recommended PR split

12 slices on one branch is too large for a meaningful review. Recommended
split is **two PRs**, both targeting `main`:

### PR #1 — `Slices 1–6: foundation → emergency hardening`

Range: `<empty>..2e92… (Slice 6 review #2 tip)` — confirm with
`git log --reverse --oneline` and pick the last `feat:`/`fix:` commit
that lands the Slice 6 emergency hardening work.

Claims:
- Monorepo skeleton + docker-compose + CI (Slice 1)
- OTP auth, JWT, RBAC, append-only audit (Slice 2)
- Resident onboarding + consent + medical profile (Slice 3)
- Medical records upload + signed links (Slice 4)
- Emergency happy path (Slice 5)
- Emergency hardening: 3-channel fan-out, on-call schedules, 60s backup
  escalation, mobile offline retry, fallback action sheet (Slice 6)

Verification proof in the PR body:
- `pytest` green (whatever count was at Slice 6 tip — recover from
  `git log --grep="passed"` or by checking out the SHA and running it)
- `flutter analyze` clean, `flutter test` green
- Shared-types / dashboard typecheck + lint + build clean
- `docs/compliance-notes.md` open `[NEEDS_LEGAL_REVIEW]` items 1–9 named
  but **not silently resolved** (per brief §13)

### PR #2 — `Slices 7–12: case lifecycle, handover PDF, medicines, admin, polish, hardening`

Range: PR #1 tip..`cdcd39a` (current Slice 12 tip).

Claims:
- Case lifecycle, vitals, telemedicine notes (Slice 7)
- Hospital handover PDF + email/WhatsApp dispatch (Slice 8)
- Medicine reminders + adherence (Slice 9)
- Admin KPIs + monthly exports (Slice 10)
- Mobile polish — disclaimer ack, sticky footer, Call 108 on splash/login,
  Connected devices, offline indicator, elderly-UX theme (Slice 11)
- Emergency hardening — fallback-tap outbox, stuck-claim reaper, 2G/load
  proof, 92.53% emergency API/service coverage (Slice 12)

Verification proof in the PR body — current baseline:
- Backend: **152 pytest** passing (67 emergency + 16 handover + 23 medicines
  + 2 admin + 44 RBAC matrix + others), `ruff` clean, `alembic check` clean
- Mobile: `flutter analyze` clean, **19 flutter tests** passing
- Shared-types / dashboard typecheck + lint + build clean
- `docs/slice12-hardening-report.md` lands with the PR

## What the PRs deliberately do NOT claim

Per `PROJECT_BRIEF.md` §14 DoD, the MVP needs:
- a resident on a **real Android device** completing the full onboarding +
  emergency path, and
- the on-duty doctor receiving **push AND SMS AND a voice call** within 10 s.

Neither claim is true at Slice 12 tip:

- the mobile app's `LoginScreen.onSubmit` is null in production builds
  ([apps/mobile/lib/login.dart:81-86](apps/mobile/lib/login.dart#L81-L86));
  there is no resident profile, records, or medicines UI past Slice 11
  polish surfaces
- `TwilioNotificationGateway.send_push` raises `NotImplementedError`
  ([apps/backend/app/services/notifications.py:133-136](apps/backend/app/services/notifications.py#L133-L136));
  Slice 6's fan-out records FCM as failed and the other two channels carry
  the alert

Both gaps are **named in the PR body, not hidden** — Slice 14 closes the
mobile gap; Slice 16 closes the FCM gap. The §14 DoD claim is reserved
for after both ship.

## What this slice does NOT do

- It does **not** merge anything. The merge happens after PR review (a
  separate, user-driven step).
- It does **not** rewrite history. No squash, no rebase across branches
  — the 26-commit history of Slices 1–12 is the audit trail.
- It does **not** push to `main`. Cadence rule from `PLAN.md` /
  `docs/SLICE11-HANDOFF.md` is unchanged.
- It does **not** introduce a new branch for Slice 14. Slice 14 continues
  on `slice-6-emergency-hardening` (the user-stated working base), and
  folds into a later PR or its own once the resident path is alive.

## Suggested next actions for the user

1. `git push -u origin slice-6-emergency-hardening` (publish the 4 unpushed
   commits).
2. Open PR #1 against `main` using the range above; let CI run.
3. Open PR #2 against `main` (or against the PR #1 base if you prefer
   stacked PRs).
4. Triage the 9 `[NEEDS_LEGAL_REVIEW]` items in `docs/compliance-notes.md`
   with the legal advisor — these are blockers for real launch, but not
   for PR review.

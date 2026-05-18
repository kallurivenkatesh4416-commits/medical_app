# UX Copy — Canonical Strings

> Single source of truth for user-facing safety copy. The brief forbids diagnosis
> language anywhere (UI, errors, logs, PDF). These are the **positive** strings that
> replace it. Every string is wrapped in `tr('...')` in code so Telugu/Hindi can be
> added in Phase 2. Do not paraphrase these in components — reference the key.
>
> **Wording history:** Addition 4 originally proposed "This app does not diagnose or
> treat illness…". That used the word *diagnose* and was reverted on review to the
> brief §2.1 exact wording below. Do not reintroduce diagnose / diagnosis /
> diagnostic anywhere.

## Disclaimers

### `DISCLAIMER_FULL`
Shown once during onboarding as a **required acknowledgment** (logged to `audit_log`).

> "This app does not replace emergency hospital care. It alerts qualified medical
> staff, records health information, and supports emergency coordination. Final
> clinical decisions remain with the registered doctor. In a life-threatening
> situation, call 108 / 112 immediately."

### `DISCLAIMER_SHORT`
Sticky footer on the home, vitals, and records screens.

> "This app does not replace emergency hospital care. In a life-threatening
> situation, call 108 / 112 immediately."

## Emergency

### `OFFLINE_EMERGENCY_BUTTON`
Always visible on splash + login screens, even offline / not logged in. `tel:108`
deep link, reachable in ≤ 2 taps from any screen.

> "Call 108"

## Settings

### `CONNECTED_DEVICES_PHASE2`
Settings → "Connected devices" screen. No connect buttons, no fake placeholders.
Same line appears in `docs/demo-script.md`.

> "Wearable integration (Google Health Connect on Android, Apple HealthKit on iOS)
> is planned for Phase 2. The MVP supports manual vitals entry by clinical staff."

## Wording rules (enforced in review + tests)

- Never use diagnostic-conclusion language: "diagnose", "diagnosis", "diagnostic",
  "you have <disease>", "heart attack detected", "stroke detected".
- The only sanctioned abnormal-reading phrase (brief §2.1): **"Abnormal reading
  detected. Doctor review required."**
- All health-insight screens carry `DISCLAIMER_SHORT`.

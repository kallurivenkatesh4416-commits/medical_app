# Demo Script

> The end-to-end story to walk a builder/stakeholder through. Filled out as slices
> land; the §14 DoD run is the final form.

## Target story (brief §1)

Resident taps one button → on-station doctor alerted within seconds via push + SMS +
voice → doctor opens full patient profile/history → nurse records vitals → if needed
a Hospital Handover PDF is generated and shared with the partner hospital before the
patient arrives.

## Expectation-setting lines (say these out loud)

- "Wearable integration (Google Health Connect on Android, Apple HealthKit on iOS)
  is planned for Phase 2. The MVP supports manual vitals entry by clinical staff."
- "This app does not diagnose or treat illness — final clinical decisions remain
  with the registered doctor."

## Run (grows per slice)

- Slice 1: `docker compose up` → `/healthz` + `/readyz` green, `/docs` loads.

_Status: skeleton (Slice 1)._

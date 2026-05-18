# Resident Onboarding Flow

> Built in Slice 3. Authoritative copy: `ux-copy.md`. Consent model: `../PLAN.md`.

## Screen order (not skippable)

```
OTP login
  → Name + DOB + gender + blood group
  → Flat / villa + project lookup
  → Emergency contacts (min 1, max 3)
  → DISCLAIMER_FULL (required acknowledgment, logged to audit_log)
  → Consent screens — one per consent_type, each with a plain-English summary
    and a "Read full policy" link:
        data_storage                      (required; declining = account-closure flow)
        emergency_share_with_doctor
        emergency_share_with_hospital
        family_member_access
        medicine_reminder_notifications
  → Medical history (diseases, allergies, current medicines, surgeries,
        preferred hospital)
  → Done
```

## Rules

- Only `data_storage` is non-declinable. Declining any other consent gracefully
  disables the dependent feature (e.g. declining `family_member_access` hides the
  family-link UI).
- Each consent row stores `granted_at` / `revoked_at` / `policy_version`. A policy
  change forces re-consent (trigger definition is `[NEEDS_LEGAL_REVIEW]`).
- Every grant / revoke / re-consent writes to `audit_log`.

_Status: skeleton (Slice 1). Implemented in Slice 3._

/// Canonical safety / UX copy — single source of truth (`docs/ux-copy.md`).
///
/// **Do not paraphrase** any of these strings in components. The brief §2.1
/// forbids diagnosis language anywhere; the wording history note in
/// `docs/ux-copy.md` records that paraphrasing has already reintroduced
/// the word "diagnose" once and was reverted. The widget tests assert
/// against the literal constants below.
///
/// Every user-facing string is wrapped in `tr('...')` at the call site so
/// Telugu and Hindi can land in Phase 2 by swapping the `tr` implementation.
/// `tr` itself stays the identity function in this slice (see `main.dart`).

library;

/// Sticky footer on every health-insight screen (home / vitals / records).
const String disclaimerShort =
    'This app does not replace emergency hospital care. '
    'In a life-threatening situation, call 108 / 112 immediately.';

/// One-time onboarding acknowledgment — required before consents and
/// profile are persisted. The tap is recorded in the backend `audit_log`
/// as `DISCLAIMER_ACKNOWLEDGED` inside the onboarding transaction.
const String disclaimerFull =
    'This app does not replace emergency hospital care. It alerts qualified '
    'medical staff, records health information, and supports emergency '
    'coordination. Final clinical decisions remain with the registered '
    'doctor. In a life-threatening situation, call 108 / 112 immediately.';

/// Settings → Connected devices. Slice 11 ships the copy only — no connect
/// buttons, no fake "Coming Soon" UI, no device list.
const String connectedDevicesPhase2 =
    'Wearable integration (Google Health Connect on Android, Apple HealthKit '
    'on iOS) is planned for Phase 2. The MVP supports manual vitals entry '
    'by clinical staff.';

/// The only hardcoded emergency-call label (brief §2.2). Used by both the
/// splash and the login surfaces so widget tests can assert ≤2-tap reach
/// against the same literal.
const String callOneZeroEightLabel = 'Call 108';

/// Banner copy when `ConnectivityWatcher` reports offline. Reassuring,
/// non-alarming, surfaces the in-flight emergency queue invariant from
/// Slice 6.
const String offlineBanner =
    'No connection. Emergency alerts will be retried automatically when '
    'you are back online.';

/// Generic empty-state copy used across list surfaces. Replaces the
/// platform "no items" with something that names the state plainly.
const String emptyStateMedicines = 'No medicines on your schedule yet.';
const String emptyStateRecords = 'No medical records uploaded yet.';
const String emptyStateVitals = 'No vitals recorded yet.';

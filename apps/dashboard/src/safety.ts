/**
 * Slice 15 — canonical safety / UX copy for the staff dashboard.
 *
 * Mirrors `apps/mobile/lib/safety.dart`. The wording history in
 * `docs/ux-copy.md` records that paraphrasing reintroduced the word
 * "diagnose" once and was reverted — do NOT paraphrase these strings
 * in components, import the constants.
 */

export const DISCLAIMER_SHORT =
  "This app does not replace emergency hospital care. " +
  "In a life-threatening situation, call 108 / 112 immediately.";

export const EMPTY_ALERT_FEED = "No active emergency alerts.";
export const EMPTY_RECORDS = "No medical records uploaded yet.";
export const EMPTY_MEDICINES = "No medicines on the resident's schedule yet.";
export const EMPTY_VITALS = "No vitals recorded for this case yet.";
export const EMPTY_NOTES = "No notes recorded for this case yet.";

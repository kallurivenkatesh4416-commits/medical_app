/// PLAN.md Slice 16 — mobile FCM seam.
///
/// User decision on Slice 16 mobile depth: **Option A** — seam only, do
/// not install `firebase_messaging` until the Firebase project / config
/// files are ready. Same pattern as the Slice 6/8/9 plugins (Twilio /
/// FCM live wiring deferred until operator keys land).
///
/// The seam is a typedef instead of an abstract class so production can
/// wire a one-line `FirebaseMessaging.instance.getToken` later without
/// changing any call sites. Today the default returns null; the
/// post-login wiring in `main.dart` skips registration when the
/// provider gives back null, so the app stays fully functional with no
/// Firebase project provisioned.
///
/// Mirrors the way the Slice 14 `tokenProvider` closure on EmergencyApi
/// reads the JWT from AuthStorage — both are "ask at the point of need"
/// async getters that production binds at startup.

library;

/// Returns the current FCM device token, or null if the platform has not
/// minted one (no Firebase project provisioned, user denied notification
/// permission, or running in a unit test).
typedef PushTokenProvider = Future<String?> Function();

/// Default seam used when production has not registered a real provider.
/// Always returns null so the post-login registration step becomes a
/// no-op — the app continues without Firebase wired.
Future<String?> nullPushTokenProvider() async => null;

/// Stable wire-token for the `platform` field on the backend
/// `/api/v1/me/device-tokens` endpoint. The Slice 16 backend audit row
/// stores this verbatim; do not paraphrase here without bumping the
/// audit-meta schema in `app/services/emergency_service.py`.
String defaultDevicePlatform() {
  // dart:io would surface the real platform; we keep this as a constant
  // for the seam-only build (no platform-channel touchpoints). The future
  // FirebaseMessaging adapter overrides this when it lands.
  return 'unknown';
}

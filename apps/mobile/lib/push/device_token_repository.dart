/// PLAN.md Slice 16 — mobile FCM device-token + delivery-ack HTTP surface.
///
/// Backend contract: `app/api/notifications.py` + `app/api/emergency.py`.
/// Two endpoints:
///
/// - `POST /api/v1/me/device-tokens`
///     Resident-side counterpart to the Slice 5 staff endpoint. The
///     Slice 14 Flutter app calls this once a Firebase token is
///     available (today: never; the seam returns null — see
///     `push_token_provider.dart` for the deferred Option-A wiring).
/// - `POST /api/v1/notifications/fcm/ack`
///     Mobile-side acknowledgment that an FCM push arrived. Updates the
///     backend `notification_attempts.status` to `delivered` so the
///     Slice 6 trail closes the loop on real-world push outcomes.
///
/// Both calls go through [ApiClient] so the AuthStorage token + the
/// 401-refresh-retry path from Slice 14 stay intact. The repo is the
/// same function-shaped seam style as `AuthRepository` and
/// `RecordsRepository` so widget tests stub the HTTP layer without
/// touching real plugins.

library;

import 'dart:async';

import '../api_client.dart';

typedef RegisterDeviceTokenCall = Future<ApiResponse> Function(
  String token,
  String platform,
);

typedef AcknowledgeFcmCall = Future<ApiResponse> Function(String attemptId);

class DeviceTokenRepository {
  DeviceTokenRepository({
    required this.registerTokenCall,
    required this.acknowledgeCall,
  });

  final RegisterDeviceTokenCall registerTokenCall;
  final AcknowledgeFcmCall acknowledgeCall;

  /// Returns true on a 2xx server response. The caller treats a false
  /// result as "skip — try again next login" rather than surfacing it
  /// to the resident: a missing push token is not blocking, the SMS
  /// + voice channels still fire (Slice 6 fan-out invariant).
  Future<bool> registerToken({required String token, required String platform}) async {
    if (token.isEmpty) return false;
    final resp = await registerTokenCall(token, platform);
    return resp.ok;
  }

  /// Called by the mobile push handler when an FCM message arrives.
  /// Slice 16 review #1: the device reads `attempt_id` (a UUID string)
  /// out of the FCM `data` payload — NOT `provider_ref`. FCM only
  /// returns its own `name` after the backend's send call completes,
  /// so the device cannot echo it back; the backend threads the
  /// pre-existing `notification_attempts.id` through the data payload
  /// instead. Returns true on a 2xx; caller logs but does not retry
  /// on failure — the SMS + voice channels are still the Slice 6
  /// fan-out's safety net.
  Future<bool> acknowledgePush(String attemptId) async {
    if (attemptId.isEmpty) return false;
    final resp = await acknowledgeCall(attemptId);
    return resp.ok;
  }
}

DeviceTokenRepository buildDeviceTokenRepository(ApiClient client) {
  return DeviceTokenRepository(
    registerTokenCall: (token, platform) => client.postJson(
      '/api/v1/me/device-tokens',
      body: {'token': token, 'platform': platform},
    ),
    acknowledgeCall: (attemptId) => client.postJson(
      '/api/v1/notifications/fcm/ack',
      body: {'attempt_id': attemptId},
    ),
  );
}

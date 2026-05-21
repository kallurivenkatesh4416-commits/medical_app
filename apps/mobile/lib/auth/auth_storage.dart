/// PLAN.md Slice 14 — JWT + registration-grant storage seam.
///
/// The resident's access + refresh tokens never touch disk in cleartext.
/// Production uses `flutter_secure_storage` (Keystore on Android, Keychain
/// on iOS); widget tests use the in-memory implementation so no platform
/// channel mock is needed.
///
/// Brief §10: "Idempotency keys are not secrets, tokens are." The mobile
/// pending-alert idempotency key is intentionally stored on plain disk
/// ([emergency_api.dart:11](../emergency_api.dart) `FilePendingAlertStore`)
/// because it is an opaque client token with no PII/PHI. Access/refresh
/// tokens are a different class of secret and stay in secure storage.

library;

import 'dart:async';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

abstract class AuthStorage {
  Future<String?> readAccessToken();
  Future<String?> readRefreshToken();
  Future<String?> readRegistrationToken();

  /// Atomic replace — used on successful login / refresh / onboarding.
  Future<void> saveSession({
    required String accessToken,
    required String refreshToken,
  });

  /// Short-lived registration grant returned by OTP verify when no account
  /// exists yet; consumed by `GET /projects` and `POST /onboarding/complete`.
  Future<void> saveRegistrationToken(String registrationToken);

  Future<void> clear();
}

/// Test/dev stub. Holds tokens in memory; never reaches Keystore/Keychain.
class InMemoryAuthStorage implements AuthStorage {
  String? _access;
  String? _refresh;
  String? _registration;

  @override
  Future<String?> readAccessToken() async => _access;

  @override
  Future<String?> readRefreshToken() async => _refresh;

  @override
  Future<String?> readRegistrationToken() async => _registration;

  @override
  Future<void> saveSession({
    required String accessToken,
    required String refreshToken,
  }) async {
    _access = accessToken;
    _refresh = refreshToken;
    _registration = null; // Login supersedes any unfinished registration.
  }

  @override
  Future<void> saveRegistrationToken(String registrationToken) async {
    _registration = registrationToken;
  }

  @override
  Future<void> clear() async {
    _access = null;
    _refresh = null;
    _registration = null;
  }
}

/// Production wiring. `flutter_secure_storage` requires a platform channel;
/// tests must NOT use this class — they wire [InMemoryAuthStorage] through
/// constructor injection.
class SecureFlutterAuthStorage implements AuthStorage {
  SecureFlutterAuthStorage({FlutterSecureStorage? storage})
      : _store = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
              iOptions: IOSOptions(
                accessibility: KeychainAccessibility.first_unlock_this_device,
              ),
            );

  final FlutterSecureStorage _store;

  static const _kAccess = 'auth.access_token';
  static const _kRefresh = 'auth.refresh_token';
  static const _kRegistration = 'auth.registration_token';

  @override
  Future<String?> readAccessToken() => _store.read(key: _kAccess);

  @override
  Future<String?> readRefreshToken() => _store.read(key: _kRefresh);

  @override
  Future<String?> readRegistrationToken() => _store.read(key: _kRegistration);

  @override
  Future<void> saveSession({
    required String accessToken,
    required String refreshToken,
  }) async {
    await _store.write(key: _kAccess, value: accessToken);
    await _store.write(key: _kRefresh, value: refreshToken);
    await _store.delete(key: _kRegistration);
  }

  @override
  Future<void> saveRegistrationToken(String registrationToken) =>
      _store.write(key: _kRegistration, value: registrationToken);

  @override
  Future<void> clear() async {
    await _store.delete(key: _kAccess);
    await _store.delete(key: _kRefresh);
    await _store.delete(key: _kRegistration);
  }
}

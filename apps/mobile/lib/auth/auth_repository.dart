/// PLAN.md Slice 14 — resident auth flow.
///
/// Surfaces:
///
/// - `requestOtp(phone)` — fires `POST /api/v1/auth/otp/request`. Backend
///   returns `dev_otp` only when `APP_ENV=local`; production never includes
///   it (Slice 2 invariant). The dev value is forwarded to the UI so the
///   demo can auto-fill the code on a dev backend.
/// - `verifyOtp(phone, code)` — fires `POST /auth/otp/verify`. Three result
///   shapes: existing-account login (access/refresh tokens persisted),
///   new-phone registration (short-lived grant persisted; UI routes to
///   onboarding), or failure (no tokens touched).
/// - `refresh()` — rotates the refresh token; ApiClient calls this once on
///   401 to recover before bouncing the user back to the splash.
/// - `me()` — pulls the bearer's `User` record so the AuthGate can decide
///   between the splash and the HomeShell at app launch.
/// - `logout()` — clears storage + revokes the refresh token server-side.
///
/// Every backend interaction is funnelled through a typedef so widget tests
/// inject in-memory fakes (same pattern as `EmergencyController` /
/// `MedicineController`).

library;

import 'dart:async';

import '../api_client.dart';
import 'auth_storage.dart';

class OtpRequestResult {
  const OtpRequestResult({required this.ok, this.devOtp, this.errorCode});

  final bool ok;
  final String? devOtp;
  final String? errorCode;
}

class VerifyOutcome {
  const VerifyOutcome({
    required this.ok,
    this.loggedIn = false,
    this.registrationRequired = false,
    this.errorCode,
  });

  final bool ok;
  final bool loggedIn;
  final bool registrationRequired;
  final String? errorCode;

  static const failure = VerifyOutcome(ok: false);
}

class UserSummary {
  const UserSummary({
    required this.id,
    required this.phone,
    required this.role,
    this.projectId,
    this.fullName,
  });

  final String id;
  final String phone;
  final String role;
  final String? projectId;
  final String? fullName;

  factory UserSummary.fromJson(Map<String, dynamic> json) => UserSummary(
        id: json['id'] as String,
        phone: json['phone'] as String,
        role: json['role'] as String,
        projectId: json['project_id'] as String?,
        fullName: json['full_name'] as String?,
      );
}

/// HTTP seams — widget tests inject in-memory fakes instead of standing up
/// HttpClient mocks. Production wires these to [ApiClient] methods.
typedef OtpRequestCall = Future<ApiResponse> Function(String phone);
typedef OtpVerifyCall = Future<ApiResponse> Function(String phone, String code);
typedef RefreshCall = Future<ApiResponse> Function(String refreshToken);
typedef LogoutCall = Future<ApiResponse> Function(String refreshToken);
typedef MeCall = Future<ApiResponse> Function();

class AuthRepository {
  AuthRepository({
    required this.storage,
    required this.requestOtpCall,
    required this.verifyOtpCall,
    required this.refreshCall,
    required this.logoutCall,
    required this.meCall,
  });

  final AuthStorage storage;
  final OtpRequestCall requestOtpCall;
  final OtpVerifyCall verifyOtpCall;
  final RefreshCall refreshCall;
  final LogoutCall logoutCall;
  final MeCall meCall;

  Future<OtpRequestResult> requestOtp(String phone) async {
    final resp = await requestOtpCall(phone);
    if (!resp.ok) {
      return OtpRequestResult(ok: false, errorCode: resp.errorCode);
    }
    final devOtp = resp.json?['dev_otp'];
    return OtpRequestResult(
      ok: true,
      devOtp: devOtp is String ? devOtp : null,
    );
  }

  Future<VerifyOutcome> verifyOtp(String phone, String code) async {
    final resp = await verifyOtpCall(phone, code);
    if (!resp.ok || resp.json == null) {
      return VerifyOutcome(ok: false, errorCode: resp.errorCode);
    }
    final body = resp.json!;
    final registration = body['registration_required'] == true;
    if (registration) {
      final regToken = body['registration_token'];
      if (regToken is! String || regToken.isEmpty) {
        return const VerifyOutcome(ok: false);
      }
      await storage.saveRegistrationToken(regToken);
      return const VerifyOutcome(
        ok: true,
        loggedIn: false,
        registrationRequired: true,
      );
    }
    final access = body['access_token'];
    final refresh = body['refresh_token'];
    if (access is! String || refresh is! String) {
      return const VerifyOutcome(ok: false);
    }
    await storage.saveSession(accessToken: access, refreshToken: refresh);
    return const VerifyOutcome(ok: true, loggedIn: true);
  }

  /// Returns true on successful rotation; false means the refresh token is
  /// no longer valid (revoked, reused, expired) — caller wipes storage and
  /// routes back to the splash.
  Future<bool> refresh() async {
    final raw = await storage.readRefreshToken();
    if (raw == null || raw.isEmpty) return false;
    final resp = await refreshCall(raw);
    if (!resp.ok || resp.json == null) {
      await storage.clear();
      return false;
    }
    final access = resp.json!['access_token'];
    final newRefresh = resp.json!['refresh_token'];
    if (access is! String || newRefresh is! String) {
      await storage.clear();
      return false;
    }
    await storage.saveSession(accessToken: access, refreshToken: newRefresh);
    return true;
  }

  Future<void> logout() async {
    final raw = await storage.readRefreshToken();
    if (raw != null && raw.isNotEmpty) {
      // Best-effort: a network error must not block local logout.
      try {
        await logoutCall(raw);
      } catch (_) {
        // ignored — local clear below is what matters for re-login safety.
      }
    }
    await storage.clear();
  }

  Future<UserSummary?> me() async {
    final resp = await meCall();
    if (!resp.ok || resp.json == null) return null;
    return UserSummary.fromJson(resp.json!);
  }
}

/// Production builder: wires the repository to an [ApiClient]. Tests should
/// build `AuthRepository` directly with fake typedef closures.
AuthRepository buildAuthRepository(ApiClient client) {
  Future<ApiResponse> requestOtp(String phone) =>
      client.postJson('/api/v1/auth/otp/request', body: {'phone': phone});
  Future<ApiResponse> verifyOtp(String phone, String code) => client.postJson(
        '/api/v1/auth/otp/verify',
        body: {'phone': phone, 'code': code},
      );
  Future<ApiResponse> refresh(String raw) =>
      client.postJson('/api/v1/auth/refresh', body: {'refresh_token': raw});
  Future<ApiResponse> logout(String raw) =>
      client.postJson('/api/v1/auth/logout', body: {'refresh_token': raw});
  Future<ApiResponse> me() => client.getJson('/api/v1/auth/me');

  final repo = AuthRepository(
    storage: client.storage,
    requestOtpCall: requestOtp,
    verifyOtpCall: verifyOtp,
    refreshCall: refresh,
    logoutCall: logout,
    meCall: me,
  );
  // Wire ApiClient's auto-refresh hook to the repository so a 401 on any
  // call (records / medicines / profile) recovers without bouncing the user.
  client.refreshTokens = repo.refresh;
  return repo;
}

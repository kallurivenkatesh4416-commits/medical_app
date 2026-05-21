/// PLAN.md Slice 14 — resident onboarding HTTP surface.
///
/// Backend contract: `docs/onboarding-flow.md` + `api/onboarding.py`. Both
/// endpoints are gated by the **registration grant** the OTP verify step
/// hands back — *not* the access token (the user has no account yet).
/// Slice 14 stores the registration grant in [AuthStorage] and the repo
/// reads it from there on every call; the call sites override the
/// `authorization` header so [ApiClient] does not silently send the access
/// token (which is normally null at this point anyway).
///
/// The repo also persists the token pair the `POST /onboarding/complete`
/// response returns, so the resident lands on the HomeShell already
/// authenticated — no re-login between onboarding and the first home tap.

library;

import 'dart:async';
import 'dart:convert';

import 'api_client.dart';
import 'auth/auth_storage.dart';

class OnboardingProject {
  const OnboardingProject({required this.id, required this.name});

  final String id;
  final String name;

  factory OnboardingProject.fromJson(Map<String, dynamic> json) =>
      OnboardingProject(
        id: json['id'] as String,
        name: json['name'] as String,
      );
}

class OnboardingContact {
  const OnboardingContact({
    required this.name,
    required this.phone,
    this.relation,
    this.isPrimary = false,
  });

  final String name;
  final String phone;
  final String? relation;
  final bool isPrimary;

  Map<String, dynamic> toJson() => {
        'name': name,
        'phone': phone,
        if (relation != null && relation!.isNotEmpty) 'relation': relation,
        'is_primary': isPrimary,
      };
}

/// Five consent types — `docs/ux-copy.md` and `app/enums.py:ONBOARDING_CONSENTS`.
/// `data_storage` cannot be declined; the onboarding submit refuses the
/// payload if it is false.
enum OnboardingConsent {
  dataStorage('data_storage', required: true),
  emergencyShareWithDoctor('emergency_share_with_doctor'),
  emergencyShareWithHospital('emergency_share_with_hospital'),
  familyMemberAccess('family_member_access'),
  medicineReminderNotifications('medicine_reminder_notifications');

  const OnboardingConsent(this.wire, {this.required = false});
  final String wire;
  final bool required;
}

class OnboardingPayload {
  const OnboardingPayload({
    required this.fullName,
    required this.dob,
    required this.gender,
    required this.projectId,
    required this.flatVillaNumber,
    required this.contacts,
    required this.consents,
    this.bloodGroup,
    this.diseases = const [],
    this.allergies = const [],
    this.surgeries = const [],
    this.preferredHospital,
  });

  final String fullName;
  final DateTime dob;
  final String gender;
  final String projectId;
  final String flatVillaNumber;
  final List<OnboardingContact> contacts;
  final Map<OnboardingConsent, bool> consents;
  final String? bloodGroup;
  final List<String> diseases;
  final List<String> allergies;
  final List<String> surgeries;
  final String? preferredHospital;

  Map<String, dynamic> toJson() => {
        'full_name': fullName,
        'dob': formatYyyyMmDd(dob),
        'gender': gender,
        'project_id': projectId,
        'flat_villa_number': flatVillaNumber,
        'emergency_contacts': contacts.map((c) => c.toJson()).toList(),
        'disclaimer_acknowledged': true,
        'consents': [
          for (final c in OnboardingConsent.values)
            {
              'consent_type': c.wire,
              'granted': consents[c] ?? false,
            },
        ],
        if (bloodGroup != null && bloodGroup!.isNotEmpty) 'blood_group': bloodGroup,
        if (diseases.isNotEmpty) 'diseases': diseases,
        if (allergies.isNotEmpty) 'allergies': allergies,
        if (surgeries.isNotEmpty) 'surgeries': surgeries,
        if (preferredHospital != null && preferredHospital!.isNotEmpty)
          'preferred_hospital': preferredHospital,
        'insurance': const <String, dynamic>{},
      };

}

/// Backend speaks ISO date strings on the `dob` / `start_date` / `end_date`
/// fields. Single helper so the onboarding wizard and the medicine schedule
/// editor format identically.
String formatYyyyMmDd(DateTime dt) =>
    '${dt.year.toString().padLeft(4, "0")}-'
    '${dt.month.toString().padLeft(2, "0")}-'
    '${dt.day.toString().padLeft(2, "0")}';

class OnboardingResult {
  const OnboardingResult({required this.ok, this.errorCode});
  final bool ok;
  final String? errorCode;

  static const failure = OnboardingResult(ok: false);
}

/// Function-shaped HTTP seams so widget tests inject in-memory fakes.
typedef ListProjectsCall = Future<ApiResponse> Function(String registrationToken);
typedef CompleteOnboardingCall = Future<ApiResponse> Function(
  String registrationToken,
  Map<String, dynamic> body,
  String idempotencyKey,
);

class OnboardingRepository {
  OnboardingRepository({
    required this.storage,
    required this.listProjectsCall,
    required this.completeOnboardingCall,
  });

  final AuthStorage storage;
  final ListProjectsCall listProjectsCall;
  final CompleteOnboardingCall completeOnboardingCall;

  /// Cached idempotency key — keeps a network-flake retry deduped by the
  /// same key (backend invariant: same key + same body + same owner →
  /// re-issue tokens for the original account).
  String? _pendingIdempotencyKey;

  Future<List<OnboardingProject>> listProjects() async {
    final token = await storage.readRegistrationToken();
    if (token == null || token.isEmpty) return const [];
    final resp = await listProjectsCall(token);
    if (!resp.ok || resp.body.isEmpty) return const [];
    try {
      // /projects returns a top-level JSON array, not the envelope ApiResponse
      // auto-parses, so decode manually here.
      final decoded = jsonDecode(resp.body);
      if (decoded is! List) return const [];
      return decoded
          .whereType<Map<String, dynamic>>()
          .map(OnboardingProject.fromJson)
          .toList(growable: false);
    } catch (_) {
      return const [];
    }
  }

  Future<OnboardingResult> complete(OnboardingPayload payload) async {
    final token = await storage.readRegistrationToken();
    if (token == null || token.isEmpty) {
      return const OnboardingResult(ok: false, errorCode: 'registration_expired');
    }
    final key = _pendingIdempotencyKey ??= newIdempotencyKey();
    final resp = await completeOnboardingCall(token, payload.toJson(), key);
    if (!resp.ok || resp.json == null) {
      return OnboardingResult(ok: false, errorCode: resp.errorCode);
    }
    final body = resp.json!;
    final access = body['access_token'];
    final refresh = body['refresh_token'];
    if (access is! String || refresh is! String) {
      return const OnboardingResult(ok: false);
    }
    await storage.saveSession(accessToken: access, refreshToken: refresh);
    _pendingIdempotencyKey = null;
    return const OnboardingResult(ok: true);
  }
}

/// Production builder. Wires the repo against an [ApiClient] — same shape
/// as `buildAuthRepository`. Both endpoints override the `Authorization`
/// header so the registration grant is sent instead of any stored access
/// token (extraHeaders applies after the access-token header inside
/// ApiClient, so the override wins).
OnboardingRepository buildOnboardingRepository(ApiClient client) {
  Future<ApiResponse> listProjects(String regToken) =>
      client.getJson('/api/v1/projects', extraHeaders: {
        'authorization': 'Bearer $regToken',
      });

  Future<ApiResponse> complete(
    String regToken,
    Map<String, dynamic> body,
    String idempotencyKey,
  ) =>
      client.postJson(
        '/api/v1/onboarding/complete',
        body: body,
        extraHeaders: {'authorization': 'Bearer $regToken'},
        idempotencyKey: idempotencyKey,
      );

  return OnboardingRepository(
    storage: client.storage,
    listProjectsCall: listProjects,
    completeOnboardingCall: complete,
  );
}

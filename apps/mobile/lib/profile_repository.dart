/// PLAN.md Slice 14 — resident profile + consent HTTP surface.
///
/// Backend contract: `api/profile.py`. Slice 14 uses three of the four
/// surfaces (`GET /me/profile`, `GET /me/consents`, `PATCH /me/consents/{type}`).
/// Editing the resident profile post-onboarding is **out of scope per the
/// approved Slice 14 fork** — there is no `PATCH /me/profile` endpoint on
/// the backend yet, and the user asked for "pure-mobile, view-only edits
/// via onboarding" so we surface profile data as read-only.
///
/// Consent toggles are live: `PATCH /me/consents/{type}` is the contract
/// that backs the Slice 11 Settings → consent surface; the next staff
/// access check (records, handover, medicine reminders) reads the new
/// state immediately.

library;

import 'dart:async';
import 'dart:convert';

import 'api_client.dart';

class ResidentProfile {
  const ResidentProfile({
    required this.fullName,
    required this.phone,
    required this.flatVillaNumber,
    this.dob,
    this.gender,
    this.bloodGroup,
    this.diseases = const [],
    this.allergies = const [],
    this.surgeries = const [],
    this.preferredHospital,
    this.primaryContactName,
    this.primaryContactPhone,
  });

  final String fullName;
  final String phone;
  final String flatVillaNumber;
  final String? dob;
  final String? gender;
  final String? bloodGroup;
  final List<String> diseases;
  final List<String> allergies;
  final List<String> surgeries;
  final String? preferredHospital;
  final String? primaryContactName;
  final String? primaryContactPhone;

  factory ResidentProfile.fromJson(Map<String, dynamic> json) {
    // `jsonDecode` returns `Map<String, dynamic>` for real responses, but
    // in-memory test fixtures can carry `Map<dynamic, dynamic>` literals
    // — accept either by coercing at the boundary.
    Map<String, dynamic> asMap(Object? raw) {
      if (raw is Map<String, dynamic>) return raw;
      if (raw is Map) return Map<String, dynamic>.from(raw);
      return const {};
    }

    final medical = asMap(json['medical_profile']);
    final contacts = (json['emergency_contacts'] as List?)
            ?.map(asMap)
            .toList(growable: false) ??
        const <Map<String, dynamic>>[];
    Map<String, dynamic>? primary;
    for (final c in contacts) {
      if (c['is_primary'] == true) {
        primary = c;
        break;
      }
    }
    primary ??= contacts.isNotEmpty ? contacts.first : null;
    return ResidentProfile(
      fullName: json['full_name'] as String? ?? '',
      phone: json['phone'] as String? ?? '',
      flatVillaNumber: json['flat_villa_number'] as String? ?? '',
      dob: json['dob'] as String?,
      gender: json['gender'] as String?,
      bloodGroup: medical['blood_group'] as String?,
      diseases: _strings(medical['diseases']),
      allergies: _strings(medical['allergies']),
      surgeries: _strings(medical['surgeries']),
      preferredHospital: medical['preferred_hospital'] as String?,
      primaryContactName: primary?['name'] as String?,
      primaryContactPhone: primary?['phone'] as String?,
    );
  }

  static List<String> _strings(dynamic raw) {
    if (raw is List) {
      return raw.whereType<String>().toList(growable: false);
    }
    return const [];
  }
}

class ResidentConsent {
  const ResidentConsent({required this.type, required this.granted});

  final String type;
  final bool granted;

  factory ResidentConsent.fromJson(Map<String, dynamic> json) =>
      ResidentConsent(
        type: json['consent_type'] as String,
        granted: json['granted'] as bool? ?? false,
      );
}

typedef ReadProfileCall = Future<ApiResponse> Function();
typedef ReadConsentsCall = Future<ApiResponse> Function();
typedef UpdateConsentCall = Future<ApiResponse> Function(
  String consentType,
  bool granted,
);

class ProfileRepository {
  ProfileRepository({
    required this.readProfileCall,
    required this.readConsentsCall,
    required this.updateConsentCall,
  });

  final ReadProfileCall readProfileCall;
  final ReadConsentsCall readConsentsCall;
  final UpdateConsentCall updateConsentCall;

  Future<ResidentProfile?> readProfile() async {
    final resp = await readProfileCall();
    if (!resp.ok || resp.json == null) return null;
    return ResidentProfile.fromJson(resp.json!);
  }

  Future<List<ResidentConsent>> readConsents() async {
    final resp = await readConsentsCall();
    if (!resp.ok || resp.body.isEmpty) return const [];
    try {
      final decoded = jsonDecode(resp.body);
      if (decoded is! List) return const [];
      return decoded
          .whereType<Map<String, dynamic>>()
          .map(ResidentConsent.fromJson)
          .toList(growable: false);
    } catch (_) {
      return const [];
    }
  }

  Future<bool> updateConsent({
    required String consentType,
    required bool granted,
  }) async {
    final resp = await updateConsentCall(consentType, granted);
    return resp.ok;
  }
}

ProfileRepository buildProfileRepository(ApiClient client) {
  return ProfileRepository(
    readProfileCall: () => client.getJson('/api/v1/me/profile'),
    readConsentsCall: () => client.getJson('/api/v1/me/consents'),
    updateConsentCall: (consentType, granted) => client.patchJson(
      '/api/v1/me/consents/$consentType',
      body: {'granted': granted},
    ),
  );
}

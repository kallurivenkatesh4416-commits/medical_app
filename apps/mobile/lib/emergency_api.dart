import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'emergency.dart';

/// Default filename for the pending-alert outbox. Kept stable so a user
/// upgrading the app does not lose an in-flight emergency idempotency key.
const String _kPendingAlertFileName = 'med_emergency_pending.json';

/// Default filename for the fallback-tap outbox (Slice 12).
const String _kFallbackTapsFileName = 'med_emergency_fallback_taps.json';

/// Resolves the file path for an emergency-outbox file. Production wires
/// this to an app-private directory via `path_provider`
/// (`getApplicationSupportDirectory`) at app startup; tests / dev fall
/// back to `Directory.systemTemp` so widget tests need no platform mocks.
File pendingAlertFileIn(Directory dir) =>
    File('${dir.path}/$_kPendingAlertFileName');

File fallbackTapsFileIn(Directory dir) =>
    File('${dir.path}/$_kFallbackTapsFileName');

/// Disk-backed [PendingAlertStore]. Survives an app kill so an alert started
/// while offline is resumed (with the same idempotency key) on next launch.
/// Defaults under the OS temp dir so no `path_provider` dependency is needed;
/// production can pass an app-private dir once that wiring lands.
class FilePendingAlertStore implements PendingAlertStore {
  FilePendingAlertStore({File? file})
      : _file = file ?? pendingAlertFileIn(Directory.systemTemp);

  final File _file;

  @override
  Future<PendingAlert?> load() async {
    try {
      if (!await _file.exists()) return null;
      final raw = jsonDecode(await _file.readAsString()) as Map;
      final key = raw['idempotency_key'];
      if (key is! String || key.isEmpty) return null;
      final caseId = raw['case_id'];
      return PendingAlert(
        idempotencyKey: key,
        caseId: caseId is String && caseId.isNotEmpty ? caseId : null,
      );
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> save(PendingAlert pending) async {
    try {
      await _file.writeAsString(jsonEncode({
        'idempotency_key': pending.idempotencyKey,
        'case_id': pending.caseId,
      }));
    } catch (_) {
      // Best-effort durability; a write failure must not block the alert.
    }
  }

  @override
  Future<void> clear() async {
    try {
      if (await _file.exists()) await _file.delete();
    } catch (_) {
      // Ignore — a stale file is re-validated against the server by key.
    }
  }
}

class FileFallbackTapStore implements FallbackTapStore {
  FileFallbackTapStore({File? file})
      : _file = file ?? fallbackTapsFileIn(Directory.systemTemp);

  final File _file;

  @override
  Future<List<PendingFallbackTap>> loadAll() async {
    try {
      if (!await _file.exists()) return [];
      final raw = jsonDecode(await _file.readAsString());
      if (raw is! List) return [];
      return [
        for (final item in raw)
          if (item is Map && PendingFallbackTap.fromJson(item) != null)
            PendingFallbackTap.fromJson(item)!,
      ];
    } catch (_) {
      return [];
    }
  }

  @override
  Future<void> save(PendingFallbackTap tap) async {
    try {
      final existing = {
        for (final t in await loadAll()) t.idempotencyKey: t,
      };
      existing[tap.idempotencyKey] = tap;
      await _file.writeAsString(
        jsonEncode(existing.values.map((t) => t.toJson()).toList()),
      );
    } catch (_) {
      // Best-effort durability; the dial already happened.
    }
  }

  @override
  Future<void> remove(String idempotencyKey) async {
    try {
      final existing = {
        for (final t in await loadAll()) t.idempotencyKey: t,
      };
      existing.remove(idempotencyKey);
      if (existing.isEmpty) {
        if (await _file.exists()) await _file.delete();
        return;
      }
      await _file.writeAsString(
        jsonEncode(existing.values.map((t) => t.toJson()).toList()),
      );
    } catch (_) {
      // A stale row is safe: the backend idempotency key dedupes replay.
    }
  }
}

/// Default network wiring for the emergency flow (PLAN.md Slice 6).
///
/// Uses `dart:io` only — no extra package dependency. The access token /
/// resident session is provided once mobile auth lands in a later slice; until
/// then requests are unauthenticated, so the app exercises exactly the
/// offline/retry → fallback path this slice delivers. All methods are injected
/// into [EmergencyController], so widget tests never touch the network.
class EmergencyApi {
  EmergencyApi({
    this.baseUrl = 'http://localhost:8000',
    this.accessToken,
    this.tokenProvider,
    Directory? appPrivateDir,
  }) : _appPrivateDir = appPrivateDir;

  final String baseUrl;
  final String? accessToken;

  /// Slice 14 — reads the live access token on every call so a post-login
  /// rotation is picked up without rebuilding the controller. Falls back
  /// to [accessToken] when null (test path). Production wires this to
  /// `AuthStorage.readAccessToken`.
  final Future<String?> Function()? tokenProvider;

  // App-private directory for the durable outboxes. When null,
  // `buildController` falls back to `Directory.systemTemp` so widget tests
  // run with no `path_provider` MethodChannel mock.
  final Directory? _appPrivateDir;

  Future<Map<String, String>> _buildHeaders() async {
    final live = await tokenProvider?.call();
    final token = (live != null && live.isNotEmpty) ? live : accessToken;
    return {
      'content-type': 'application/json',
      if (token != null && token.isNotEmpty) 'authorization': 'Bearer $token',
    };
  }

  Future<AlertResult> sendAlert(String idempotencyKey) async {
    final client = HttpClient();
    try {
      final req = await client
          .postUrl(Uri.parse('$baseUrl/api/v1/emergency/alerts'));
      (await _buildHeaders()).forEach(req.headers.set);
      req.headers.set('idempotency-key', idempotencyKey);
      req.add(utf8.encode(jsonEncode({'symptom_codes': <String>[]})));
      final resp = await req.close();
      if (resp.statusCode != 200) return const AlertResult(ok: false);
      final body = jsonDecode(await resp.transform(utf8.decoder).join());
      return AlertResult(ok: true, caseId: body['id'] as String?);
    } catch (_) {
      return const AlertResult(ok: false);
    } finally {
      client.close(force: true);
    }
  }

  Future<bool> isAcknowledged(String caseId) async {
    // Resident-owned, PHI-free status read. The /active feed is staff-only
    // (a resident token would 403), so the app must use this endpoint.
    final client = HttpClient();
    try {
      final req = await client.getUrl(
        Uri.parse('$baseUrl/api/v1/emergency/alerts/$caseId/status'),
      );
      (await _buildHeaders()).forEach(req.headers.set);
      final resp = await req.close();
      if (resp.statusCode != 200) return false;
      final j = jsonDecode(await resp.transform(utf8.decoder).join());
      return (j as Map)['acknowledged'] == true;
    } catch (_) {
      return false;
    } finally {
      client.close(force: true);
    }
  }

  Future<FallbackNumbers> fallbackNumbers(String caseId) async {
    final client = HttpClient();
    try {
      final req = await client.getUrl(
        Uri.parse('$baseUrl/api/v1/emergency/alerts/$caseId/fallback-numbers'),
      );
      (await _buildHeaders()).forEach(req.headers.set);
      final resp = await req.close();
      if (resp.statusCode != 200) return FallbackNumbers.offline;
      final j = jsonDecode(await resp.transform(utf8.decoder).join());
      return FallbackNumbers(
        emergency108: j['emergency_108'] as String,
        emergency112: j['emergency_112'] as String,
        doctor: j['doctor'] as String?,
        familyPrimary: j['family_primary'] as String?,
        securityDesk: j['security_desk'] as String?,
      );
    } catch (_) {
      return FallbackNumbers.offline;
    } finally {
      client.close(force: true);
    }
  }

  Future<void> recordFallback(
    String caseId,
    String channel,
    String idempotencyKey,
  ) async {
    final client = HttpClient();
    try {
      final req = await client.postUrl(
        Uri.parse('$baseUrl/api/v1/emergency/alerts/$caseId/fallback'),
      );
      (await _buildHeaders()).forEach(req.headers.set);
      req.headers.set('idempotency-key', idempotencyKey);
      req.add(utf8.encode(jsonEncode({'channel': channel})));
      final resp = await req.close();
      final body = await resp.transform(utf8.decoder).join();
      if (resp.statusCode < 200 || resp.statusCode >= 300) {
        throw HttpException(
          'fallback record failed: ${resp.statusCode} $body',
        );
      }
    } finally {
      client.close(force: true);
    }
  }

  EmergencyController buildController() {
    final dir = _appPrivateDir ?? Directory.systemTemp;
    return EmergencyController(
      sender: sendAlert,
      ackChecker: isAcknowledged,
      numbersFetcher: fallbackNumbers,
      recorder: recordFallback,
      store: FilePendingAlertStore(file: pendingAlertFileIn(dir)),
      fallbackStore: FileFallbackTapStore(file: fallbackTapsFileIn(dir)),
    );
  }
}

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'emergency.dart';

/// Disk-backed [PendingAlertStore]. Survives an app kill so an alert started
/// while offline is resumed (with the same idempotency key) on next launch.
/// Defaults under the OS temp dir so no `path_provider` dependency is needed;
/// production can pass an app-private dir once that wiring lands.
class FilePendingAlertStore implements PendingAlertStore {
  FilePendingAlertStore({File? file})
      : _file = file ??
            File('${Directory.systemTemp.path}/med_emergency_pending.json');

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
      : _file = file ??
            File('${Directory.systemTemp.path}/med_emergency_fallback_taps.json');

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
  });

  final String baseUrl;
  final String? accessToken;

  Map<String, String> get _headers => {
        'content-type': 'application/json',
        if (accessToken != null) 'authorization': 'Bearer $accessToken',
      };

  Future<AlertResult> sendAlert(String idempotencyKey) async {
    final client = HttpClient();
    try {
      final req = await client
          .postUrl(Uri.parse('$baseUrl/api/v1/emergency/alerts'));
      _headers.forEach(req.headers.set);
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
      _headers.forEach(req.headers.set);
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
      _headers.forEach(req.headers.set);
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
      _headers.forEach(req.headers.set);
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

  EmergencyController buildController() => EmergencyController(
        sender: sendAlert,
        ackChecker: isAcknowledged,
        numbersFetcher: fallbackNumbers,
        recorder: recordFallback,
        store: FilePendingAlertStore(),
        fallbackStore: FileFallbackTapStore(),
      );
}

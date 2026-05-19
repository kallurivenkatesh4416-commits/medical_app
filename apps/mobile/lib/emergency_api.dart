import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'emergency.dart';

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
    this.idempotencyKeyFactory = _uuidish,
  });

  final String baseUrl;
  final String? accessToken;
  final String Function() idempotencyKeyFactory;

  static String _uuidish() =>
      'm-${DateTime.now().microsecondsSinceEpoch}';

  Map<String, String> get _headers => {
        'content-type': 'application/json',
        if (accessToken != null) 'authorization': 'Bearer $accessToken',
      };

  Future<AlertResult> sendAlert() async {
    final client = HttpClient();
    try {
      final req = await client
          .postUrl(Uri.parse('$baseUrl/api/v1/emergency/alerts'));
      _headers.forEach(req.headers.set);
      req.headers.set('idempotency-key', idempotencyKeyFactory());
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
    final client = HttpClient();
    try {
      final req = await client.getUrl(
        Uri.parse('$baseUrl/api/v1/emergency/alerts/active'),
      );
      _headers.forEach(req.headers.set);
      final resp = await req.close();
      if (resp.statusCode != 200) return false;
      final list = jsonDecode(await resp.transform(utf8.decoder).join());
      for (final a in list as List) {
        if (a['id'] == caseId) return a['status'] != 'alerted';
      }
      return false;
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

  Future<void> recordFallback(String caseId, String channel) async {
    final client = HttpClient();
    try {
      final req = await client.postUrl(
        Uri.parse('$baseUrl/api/v1/emergency/alerts/$caseId/fallback'),
      );
      _headers.forEach(req.headers.set);
      req.add(utf8.encode(jsonEncode({'channel': channel})));
      await req.close();
    } catch (_) {
      // Best-effort; the dial must proceed regardless.
    } finally {
      client.close(force: true);
    }
  }

  EmergencyController buildController() => EmergencyController(
        sender: sendAlert,
        ackChecker: isAcknowledged,
        numbersFetcher: fallbackNumbers,
        recorder: recordFallback,
      );
}

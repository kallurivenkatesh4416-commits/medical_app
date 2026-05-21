/// PLAN.md Slice 14 — JSON HTTP client for the resident path.
///
/// Thin wrapper around `dart:io.HttpClient` so the codebase keeps one HTTP
/// stack (Slice 6's `emergency_api.dart` also uses `dart:io`). The wrapper:
///
/// - reads the live access token from [AuthStorage] before every call,
/// - retries once on 401 by rotating the refresh token via [refreshTokens],
///   then replays the original request,
/// - never logs the request body (PHI lives in some of these payloads),
/// - returns a structured [ApiResponse] so callers branch on status, body,
///   and error code without re-parsing JSON.
///
/// The class is **production-only**. Repositories never depend on it
/// directly; production builders thread its bound methods into typedef
/// seams that widget tests stub with in-memory fakes (same shape as
/// `EmergencyController` / `MedicineController`).

library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'auth/auth_storage.dart';

class ApiResponse {
  const ApiResponse({
    required this.statusCode,
    required this.body,
    required this.json,
  });

  final int statusCode;
  final String body;
  final Map<String, dynamic>? json;

  bool get ok => statusCode >= 200 && statusCode < 300;

  /// Backend error envelope: `{ error: { code, message, details } }`.
  String? get errorCode {
    if (json == null) return null;
    final err = json!['error'];
    if (err is Map && err['code'] is String) return err['code'] as String;
    return null;
  }

  String? get errorMessage {
    if (json == null) return null;
    final err = json!['error'];
    if (err is Map && err['message'] is String) return err['message'] as String;
    return null;
  }
}

class ApiClient {
  ApiClient({
    required this.baseUrl,
    required this.storage,
    HttpClient? httpClient,
  }) : _client = httpClient ?? HttpClient();

  final String baseUrl;
  final AuthStorage storage;
  final HttpClient _client;

  /// Called once after a 401 to rotate tokens. Production wires this to the
  /// real `/api/v1/auth/refresh`; tests can leave it null (no auto-refresh).
  Future<bool> Function()? refreshTokens;

  /// Notifies listeners when the refresh fails or no refresh token is
  /// available — UI can route back to the splash without leaking the 401.
  final StreamController<void> _sessionExpired = StreamController.broadcast();
  Stream<void> get sessionExpired => _sessionExpired.stream;

  Future<ApiResponse> getJson(String path, {Map<String, String>? extraHeaders}) =>
      _send('GET', path, headers: extraHeaders);

  Future<ApiResponse> postJson(
    String path, {
    Object? body,
    Map<String, String>? extraHeaders,
    String? idempotencyKey,
  }) =>
      _send(
        'POST',
        path,
        body: body,
        headers: {
          if (idempotencyKey != null) 'idempotency-key': idempotencyKey,
          ...?extraHeaders,
        },
      );

  Future<ApiResponse> patchJson(
    String path, {
    Object? body,
    Map<String, String>? extraHeaders,
  }) =>
      _send('PATCH', path, body: body, headers: extraHeaders);

  /// Multipart POST — used by record upload. The single file part is named
  /// `file`; additional fields go in [formFields] (records.py reads them as
  /// `Form(...)` params). Idempotency key (when supplied) rides on the
  /// header, not the body, to match Slice 4's contract.
  Future<ApiResponse> postMultipart(
    String path, {
    required String fileName,
    required String contentType,
    required List<int> fileBytes,
    required Map<String, String> formFields,
    String? idempotencyKey,
  }) async {
    final boundary = _multipartBoundary();
    final body = _buildMultipartBody(
      boundary: boundary,
      fileName: fileName,
      contentType: contentType,
      fileBytes: fileBytes,
      fields: formFields,
    );
    return _sendBytes(
      'POST',
      path,
      bodyBytes: body,
      contentType: 'multipart/form-data; boundary=$boundary',
      headers: {
        if (idempotencyKey != null) 'idempotency-key': idempotencyKey,
      },
    );
  }

  Future<ApiResponse> _send(
    String method,
    String path, {
    Object? body,
    Map<String, String>? headers,
  }) async {
    final bytes = body == null ? null : utf8.encode(jsonEncode(body));
    return _sendBytes(
      method,
      path,
      bodyBytes: bytes,
      contentType: 'application/json',
      headers: headers,
    );
  }

  Future<ApiResponse> _sendBytes(
    String method,
    String path, {
    List<int>? bodyBytes,
    String? contentType,
    Map<String, String>? headers,
  }) async {
    final firstAttempt = await _attempt(
      method,
      path,
      bodyBytes: bodyBytes,
      contentType: contentType,
      headers: headers,
    );
    if (firstAttempt.statusCode != 401) return firstAttempt;
    // Tokens may have expired between calls. Try a single refresh round-trip.
    final refresher = refreshTokens;
    if (refresher == null) {
      _sessionExpired.add(null);
      return firstAttempt;
    }
    final refreshed = await refresher();
    if (!refreshed) {
      _sessionExpired.add(null);
      return firstAttempt;
    }
    return _attempt(
      method,
      path,
      bodyBytes: bodyBytes,
      contentType: contentType,
      headers: headers,
    );
  }

  Future<ApiResponse> _attempt(
    String method,
    String path, {
    List<int>? bodyBytes,
    String? contentType,
    Map<String, String>? headers,
  }) async {
    final uri = Uri.parse('$baseUrl$path');
    final req = await _client.openUrl(method, uri);
    req.headers.set('accept', 'application/json');
    if (contentType != null) {
      req.headers.set('content-type', contentType);
    }
    final token = await storage.readAccessToken();
    if (token != null && token.isNotEmpty) {
      req.headers.set('authorization', 'Bearer $token');
    }
    if (headers != null) {
      headers.forEach(req.headers.set);
    }
    if (bodyBytes != null) {
      req.add(bodyBytes);
    }
    final resp = await req.close();
    final raw = await resp.transform(utf8.decoder).join();
    Map<String, dynamic>? parsed;
    if (raw.isNotEmpty) {
      try {
        final decoded = jsonDecode(raw);
        if (decoded is Map<String, dynamic>) parsed = decoded;
      } catch (_) {
        // Non-JSON response (e.g. a PDF byte stream); leave parsed = null.
      }
    }
    return ApiResponse(statusCode: resp.statusCode, body: raw, json: parsed);
  }

  Future<void> close() async {
    await _sessionExpired.close();
    _client.close(force: true);
  }
}

/// RFC 1341 multipart boundary — random hex, no path-traversal characters.
String _multipartBoundary() {
  final rng = Random.secure();
  final bytes = List<int>.generate(16, (_) => rng.nextInt(256));
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return 'medemergency-$hex';
}

List<int> _buildMultipartBody({
  required String boundary,
  required String fileName,
  required String contentType,
  required List<int> fileBytes,
  required Map<String, String> fields,
}) {
  final out = BytesBuilder();
  void writeLine(String s) {
    out.add(utf8.encode(s));
    out.add(const [13, 10]); // CRLF
  }

  for (final entry in fields.entries) {
    writeLine('--$boundary');
    writeLine(
      'Content-Disposition: form-data; name="${_quote(entry.key)}"',
    );
    writeLine('');
    writeLine(entry.value);
  }
  writeLine('--$boundary');
  writeLine(
    'Content-Disposition: form-data; name="file"; filename="${_quote(fileName)}"',
  );
  writeLine('Content-Type: $contentType');
  writeLine('');
  out.add(fileBytes);
  out.add(const [13, 10]);
  writeLine('--$boundary--');
  return out.toBytes();
}

String _quote(String s) => s.replaceAll('"', '\\"');

/// Lightweight UUIDv4-shaped string for `Idempotency-Key`. Not RFC-strict
/// (no version bits set) but the backend treats keys as opaque strings up
/// to 128 chars — it only checks owner + body fingerprints for conflict.
String newIdempotencyKey() {
  final rng = Random.secure();
  String hex(int n) =>
      List<int>.generate(n, (_) => rng.nextInt(256))
          .map((b) => b.toRadixString(16).padLeft(2, '0'))
          .join();
  return '${hex(4)}-${hex(2)}-${hex(2)}-${hex(2)}-${hex(6)}';
}

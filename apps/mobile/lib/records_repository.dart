/// PLAN.md Slice 14 — resident medical records HTTP surface.
///
/// Backend contract: `api/records.py`. Slice 14 wires three of the
/// resident-self endpoints:
///
/// - `GET    /api/v1/me/records`                 — list own records
/// - `POST   /api/v1/me/records` (multipart)     — upload via `file_picker`
/// - `GET    /api/v1/me/records/{id}/link`       — short-lived signed URL
///
/// Idempotency-Key on upload is computed by the repo using a UUID4-shaped
/// random token. A retry on the same `(file_name, content_type, sha256,
/// metadata)` body returns the original record (Slice 4 invariant) so a
/// flaky network can't create two rows on the resident's behalf.

library;

import 'dart:async';
import 'dart:convert';

import 'api_client.dart';

class MedicalRecord {
  const MedicalRecord({
    required this.id,
    required this.fileName,
    required this.contentType,
    required this.recordType,
    required this.sizeBytes,
    required this.createdAt,
    this.recordDate,
    this.source,
    this.tags = const [],
  });

  final String id;
  final String fileName;
  final String contentType;
  final String recordType;
  final String? recordDate;
  final String? source;
  final List<String> tags;
  final int sizeBytes;
  final String createdAt;

  factory MedicalRecord.fromJson(Map<String, dynamic> json) => MedicalRecord(
        id: json['id'] as String,
        fileName: json['file_name'] as String? ?? 'record',
        contentType:
            json['content_type'] as String? ?? 'application/octet-stream',
        recordType: json['record_type'] as String? ?? 'prescription',
        recordDate: json['record_date'] as String?,
        source: json['source'] as String?,
        tags: (json['tags'] as List?)?.whereType<String>().toList() ?? const [],
        sizeBytes: (json['size_bytes'] as num?)?.toInt() ?? 0,
        createdAt: json['created_at'] as String? ?? '',
      );
}

class RecordUploadResult {
  const RecordUploadResult({required this.ok, this.record, this.errorCode});
  final bool ok;
  final MedicalRecord? record;
  final String? errorCode;
}

typedef ListRecordsCall = Future<ApiResponse> Function();
typedef UploadRecordCall = Future<ApiResponse> Function({
  required String fileName,
  required String contentType,
  required List<int> fileBytes,
  required Map<String, String> formFields,
  required String idempotencyKey,
});
typedef LinkRecordCall = Future<ApiResponse> Function(String recordId);

class RecordsRepository {
  RecordsRepository({
    required this.listRecordsCall,
    required this.uploadRecordCall,
    required this.linkRecordCall,
  });

  final ListRecordsCall listRecordsCall;
  final UploadRecordCall uploadRecordCall;
  final LinkRecordCall linkRecordCall;

  Future<List<MedicalRecord>> list() async {
    final resp = await listRecordsCall();
    if (!resp.ok || resp.body.isEmpty) return const [];
    try {
      final decoded = jsonDecode(resp.body);
      if (decoded is! List) return const [];
      return decoded
          .whereType<Map<String, dynamic>>()
          .map(MedicalRecord.fromJson)
          .toList(growable: false);
    } catch (_) {
      return const [];
    }
  }

  Future<RecordUploadResult> upload({
    required String fileName,
    required String contentType,
    required List<int> fileBytes,
    required String recordType,
    String? recordDate,
    String? source,
    List<String> tags = const [],
  }) async {
    final form = <String, String>{
      'record_type': recordType,
      if (recordDate != null) 'record_date': recordDate,
      if (source != null && source.isNotEmpty) 'source': source,
      if (tags.isNotEmpty) 'tags': tags.join(','),
    };
    final resp = await uploadRecordCall(
      fileName: fileName,
      contentType: contentType,
      fileBytes: fileBytes,
      formFields: form,
      idempotencyKey: newIdempotencyKey(),
    );
    if (!resp.ok || resp.json == null) {
      return RecordUploadResult(ok: false, errorCode: resp.errorCode);
    }
    return RecordUploadResult(
      ok: true,
      record: MedicalRecord.fromJson(resp.json!),
    );
  }

  /// Returns the signed URL string. Caller passes it to `url_launcher` so
  /// the device opens the PDF/image in the platform's native viewer.
  Future<String?> signedLinkFor(String recordId) async {
    final resp = await linkRecordCall(recordId);
    if (!resp.ok || resp.json == null) return null;
    final url = resp.json!['url'];
    if (url is String && url.isNotEmpty) return url;
    return null;
  }
}

RecordsRepository buildRecordsRepository(ApiClient client) {
  return RecordsRepository(
    listRecordsCall: () => client.getJson('/api/v1/me/records'),
    uploadRecordCall: ({
      required fileName,
      required contentType,
      required fileBytes,
      required formFields,
      required idempotencyKey,
    }) =>
        client.postMultipart(
      '/api/v1/me/records',
      fileName: fileName,
      contentType: contentType,
      fileBytes: fileBytes,
      formFields: formFields,
      idempotencyKey: idempotencyKey,
    ),
    linkRecordCall: (recordId) =>
        client.getJson('/api/v1/me/records/$recordId/link'),
  );
}

/// PLAN.md Slice 14 — production wiring for the records upload FAB.
///
/// Kept in its own file so widget tests (which never tap the FAB on the
/// production code path) do not trigger the `file_picker` platform plugin
/// at all — the seam in `home.dart` is null in tests, and only `main.dart`
/// calls [registerRecordsPickerUploader] at startup.

library;

import 'dart:async';

import 'package:file_picker/file_picker.dart';

import 'home.dart' show recordsPickerUploader;
import 'records_repository.dart';

void registerRecordsPickerUploader() {
  recordsPickerUploader = _pickAndUpload;
}

Future<RecordUploadResult?> _pickAndUpload(RecordsRepository repo) async {
  final picked = await FilePicker.platform.pickFiles(
    type: FileType.custom,
    // Brief §10: PDF + JPEG + PNG only (matches the backend's
    // records_service allowlist).
    allowedExtensions: const ['pdf', 'jpg', 'jpeg', 'png'],
    withData: true,
  );
  if (picked == null || picked.files.isEmpty) return null;
  final f = picked.files.first;
  final bytes = f.bytes;
  if (bytes == null) return null;
  final ext = (f.extension ?? '').toLowerCase();
  final contentType = switch (ext) {
    'pdf' => 'application/pdf',
    'jpg' || 'jpeg' => 'image/jpeg',
    'png' => 'image/png',
    _ => 'application/octet-stream',
  };
  // Slice 14: the upload form only requires `record_type`. The resident
  // can tag/date later from the doctor side; the brief lets us default to
  // `prescription` for the demo path because that is the most common
  // first upload. A future slice adds an explicit type-picker dialog.
  return repo.upload(
    fileName: f.name,
    contentType: contentType,
    fileBytes: bytes,
    recordType: 'prescription',
  );
}

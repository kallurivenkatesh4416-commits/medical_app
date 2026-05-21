/// PLAN.md Slice 14 — resident medicine schedule HTTP surface.
///
/// Backend contract: `api/medicines.py`. Resident-self surface:
///
/// - `GET   /api/v1/me/medicines/schedules`            — list own active schedules
/// - `POST  /api/v1/me/medicines/schedules`            — create (consent-gated)
/// - `POST  /api/v1/me/medicines/schedules/{id}/deactivate`
/// - `POST  /api/v1/me/medicines/doses`                — log taken | skipped
///
/// The repo plugs straight into the [MedicineController] from
/// `medicine.dart` (Slice 9) — that controller already has the right
/// shape; Slice 14 just wires production HTTP into the typedef seams.

library;

import 'dart:async';
import 'dart:convert';

import 'api_client.dart';
import 'medicine.dart';

MedicineController buildMedicineController(
  ApiClient client, {
  required LocalReminderScheduler reminderScheduler,
}) {
  Future<List<MedicineSchedule>> fetchSchedules() async {
    final resp = await client.getJson('/api/v1/me/medicines/schedules');
    if (!resp.ok || resp.body.isEmpty) return const [];
    try {
      final decoded = jsonDecode(resp.body);
      if (decoded is! List) return const [];
      return decoded
          .whereType<Map<String, dynamic>>()
          .map(MedicineSchedule.fromJson)
          .toList(growable: false);
    } catch (_) {
      return const [];
    }
  }

  Future<MedicineApiResult<MedicineSchedule>> createSchedule(
    Map<String, dynamic> body,
  ) async {
    final resp = await client.postJson(
      '/api/v1/me/medicines/schedules',
      body: body,
    );
    if (!resp.ok || resp.json == null) {
      return MedicineApiResult(ok: false, errorCode: resp.errorCode);
    }
    return MedicineApiResult(
      ok: true,
      value: MedicineSchedule.fromJson(resp.json!),
    );
  }

  Future<bool> deactivate(String scheduleId) async {
    final resp = await client.postJson(
      '/api/v1/me/medicines/schedules/$scheduleId/deactivate',
    );
    return resp.ok;
  }

  Future<bool> logDose({
    required String scheduleId,
    required DateTime scheduledFor,
    required String status,
  }) async {
    final resp = await client.postJson(
      '/api/v1/me/medicines/doses',
      body: {
        'schedule_id': scheduleId,
        'scheduled_for': scheduledFor.toUtc().toIso8601String(),
        'status': status,
      },
    );
    return resp.ok;
  }

  return MedicineController(
    fetchSchedules: fetchSchedules,
    createSchedule: createSchedule,
    deactivateSchedule: deactivate,
    logDose: logDose,
    reminderScheduler: reminderScheduler,
  );
}

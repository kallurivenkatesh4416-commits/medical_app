import 'dart:async';

/// PLAN.md Slice 9 — resident medicine reminders.
///
/// Reminders are **device-local** (brief decision: no FCM dependency).
/// [LocalReminderScheduler] is the seam the production wiring sits behind;
/// the stub records intended reminders in-memory so widget tests assert the
/// contract end-to-end without standing up a notifications channel.
///
/// The real `flutter_local_notifications` integration is a thin adapter the
/// app installs at startup once Android/iOS native channels are configured;
/// adding the dep before that work would fail `flutter test` on CI with no
/// platform plugins registered.

class MedicineSchedule {
  const MedicineSchedule({
    required this.id,
    required this.name,
    required this.dose,
    required this.frequency,
    required this.timesOfDay,
    required this.active,
    this.instructions,
  });

  final String id;
  final String name;
  final String? dose;
  final String? instructions;
  final String frequency;
  final List<String> timesOfDay;
  final bool active;

  factory MedicineSchedule.fromJson(Map<String, dynamic> json) =>
      MedicineSchedule(
        id: json['id'] as String,
        name: json['name'] as String,
        dose: json['dose'] as String?,
        instructions: json['instructions'] as String?,
        frequency: json['frequency'] as String,
        timesOfDay:
            (json['times_of_day'] as List?)?.map((e) => e as String).toList() ??
                const [],
        active: json['active'] as bool? ?? true,
      );
}

class ScheduledReminder {
  const ScheduledReminder({
    required this.scheduleId,
    required this.timeOfDay,
    required this.label,
  });

  final String scheduleId;
  final String timeOfDay; // "HH:MM"
  final String label;

  @override
  bool operator ==(Object other) =>
      other is ScheduledReminder &&
      other.scheduleId == scheduleId &&
      other.timeOfDay == timeOfDay &&
      other.label == label;

  @override
  int get hashCode => Object.hash(scheduleId, timeOfDay, label);

  @override
  String toString() => 'ScheduledReminder($scheduleId, $timeOfDay, $label)';
}

/// The contract the mobile UI uses to keep device-local reminders in sync
/// with the resident's active schedules. The production implementation
/// wraps `flutter_local_notifications`; the stub keeps an in-memory map so
/// tests can assert exactly which reminders were registered.
abstract class LocalReminderScheduler {
  Future<void> syncFromSchedules(List<MedicineSchedule> schedules);

  Future<void> cancelForSchedule(String scheduleId);

  /// Returns the active reminders the device currently has registered.
  /// Used by the in-app medicine screen to render a "Upcoming today" hint
  /// and by tests to inspect dispatch behaviour.
  List<ScheduledReminder> get registered;
}

class InMemoryReminderScheduler implements LocalReminderScheduler {
  final Map<String, List<ScheduledReminder>> _byScheduleId = {};

  @override
  Future<void> syncFromSchedules(List<MedicineSchedule> schedules) async {
    // Reset on every sync — easy mental model: the server is the source of
    // truth, the device mirrors it.
    _byScheduleId.clear();
    for (final s in schedules) {
      if (!s.active) continue;
      final label = s.dose == null || s.dose!.isEmpty
          ? s.name
          : '${s.name} (${s.dose})';
      _byScheduleId[s.id] = [
        for (final hhmm in s.timesOfDay)
          ScheduledReminder(scheduleId: s.id, timeOfDay: hhmm, label: label),
      ];
    }
  }

  @override
  Future<void> cancelForSchedule(String scheduleId) async {
    _byScheduleId.remove(scheduleId);
  }

  @override
  List<ScheduledReminder> get registered =>
      [for (final list in _byScheduleId.values) ...list];
}

class MedicineApiResult<T> {
  const MedicineApiResult({required this.ok, this.value, this.errorCode});

  final bool ok;
  final T? value;
  final String? errorCode;
}

/// MedicineController orchestrates fetching the resident's schedules,
/// syncing the device-local reminder scheduler, and logging "taken" /
/// "skipped" doses. All collaborators are injected so widget tests run
/// without network or platform plugins.
class MedicineController {
  MedicineController({
    required this.fetchSchedules,
    required this.createSchedule,
    required this.deactivateSchedule,
    required this.logDose,
    required this.reminderScheduler,
  });

  final Future<List<MedicineSchedule>> Function() fetchSchedules;
  final Future<MedicineApiResult<MedicineSchedule>> Function(
    Map<String, dynamic> body,
  ) createSchedule;
  final Future<bool> Function(String scheduleId) deactivateSchedule;
  final Future<bool> Function({
    required String scheduleId,
    required DateTime scheduledFor,
    required String status,
  }) logDose;
  final LocalReminderScheduler reminderScheduler;

  List<MedicineSchedule> _schedules = const [];
  List<MedicineSchedule> get schedules => _schedules;

  Future<void> refresh() async {
    _schedules = await fetchSchedules();
    await reminderScheduler.syncFromSchedules(_schedules);
  }

  Future<MedicineApiResult<MedicineSchedule>> create({
    required String name,
    String? dose,
    String? instructions,
    required String frequency,
    required List<String> timesOfDay,
    required DateTime startDate,
    DateTime? endDate,
  }) async {
    final result = await createSchedule({
      'name': name,
      if (dose != null && dose.isNotEmpty) 'dose': dose,
      if (instructions != null && instructions.isNotEmpty)
        'instructions': instructions,
      'frequency': frequency,
      'times_of_day': timesOfDay,
      'start_date': _yyyymmdd(startDate),
      if (endDate != null) 'end_date': _yyyymmdd(endDate),
    });
    if (result.ok) await refresh();
    return result;
  }

  Future<bool> deactivate(String scheduleId) async {
    final ok = await deactivateSchedule(scheduleId);
    if (ok) {
      await reminderScheduler.cancelForSchedule(scheduleId);
      await refresh();
    }
    return ok;
  }

  Future<bool> markTaken(String scheduleId, DateTime scheduledFor) =>
      logDose(
        scheduleId: scheduleId,
        scheduledFor: scheduledFor,
        status: 'taken',
      );

  Future<bool> markSkipped(String scheduleId, DateTime scheduledFor) =>
      logDose(
        scheduleId: scheduleId,
        scheduledFor: scheduledFor,
        status: 'skipped',
      );

  static String _yyyymmdd(DateTime dt) =>
      '${dt.year.toString().padLeft(4, "0")}-'
      '${dt.month.toString().padLeft(2, "0")}-'
      '${dt.day.toString().padLeft(2, "0")}';
}

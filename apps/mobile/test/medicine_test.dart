import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/medicine.dart';

void main() {
  group('InMemoryReminderScheduler', () {
    test('syncFromSchedules registers one reminder per time-of-day slot', () async {
      final scheduler = InMemoryReminderScheduler();
      await scheduler.syncFromSchedules([
        const MedicineSchedule(
          id: 's1',
          name: 'Amlodipine',
          dose: '5 mg',
          frequency: 'twice_daily',
          timesOfDay: ['08:00', '20:00'],
          active: true,
        ),
        const MedicineSchedule(
          id: 's2',
          name: 'Atorvastatin',
          dose: '10 mg',
          frequency: 'once_daily',
          timesOfDay: ['22:00'],
          active: true,
        ),
      ]);

      final registered = scheduler.registered;
      expect(registered.length, 3);
      expect(registered, contains(
        const ScheduledReminder(
          scheduleId: 's1', timeOfDay: '08:00', label: 'Amlodipine (5 mg)',
        ),
      ));
      expect(registered, contains(
        const ScheduledReminder(
          scheduleId: 's1', timeOfDay: '20:00', label: 'Amlodipine (5 mg)',
        ),
      ));
      expect(registered, contains(
        const ScheduledReminder(
          scheduleId: 's2', timeOfDay: '22:00', label: 'Atorvastatin (10 mg)',
        ),
      ));
    });

    test('syncFromSchedules skips inactive schedules', () async {
      final scheduler = InMemoryReminderScheduler();
      await scheduler.syncFromSchedules([
        const MedicineSchedule(
          id: 's1',
          name: 'Stopped med',
          dose: '5 mg',
          frequency: 'once_daily',
          timesOfDay: ['08:00'],
          active: false,
        ),
      ]);
      expect(scheduler.registered, isEmpty);
    });

    test('cancelForSchedule drops only the targeted schedule', () async {
      final scheduler = InMemoryReminderScheduler();
      await scheduler.syncFromSchedules([
        const MedicineSchedule(
          id: 's1',
          name: 'A',
          dose: null,
          frequency: 'once_daily',
          timesOfDay: ['08:00'],
          active: true,
        ),
        const MedicineSchedule(
          id: 's2',
          name: 'B',
          dose: null,
          frequency: 'once_daily',
          timesOfDay: ['09:00'],
          active: true,
        ),
      ]);
      await scheduler.cancelForSchedule('s1');
      expect(scheduler.registered.map((r) => r.scheduleId), ['s2']);
    });
  });

  group('MedicineController', () {
    test('refresh fetches schedules and syncs the local scheduler', () async {
      final scheduler = InMemoryReminderScheduler();
      final controller = MedicineController(
        fetchSchedules: () async => [
          const MedicineSchedule(
            id: 's1',
            name: 'Amlodipine',
            dose: '5 mg',
            frequency: 'once_daily',
            timesOfDay: ['08:00'],
            active: true,
          ),
        ],
        createSchedule: (_) async => const MedicineApiResult(ok: false),
        deactivateSchedule: (_) async => false,
        logDose: ({
          required scheduleId,
          required scheduledFor,
          required status,
        }) async => false,
        reminderScheduler: scheduler,
      );

      await controller.refresh();
      expect(controller.schedules.length, 1);
      expect(scheduler.registered.length, 1);
      expect(scheduler.registered.first.timeOfDay, '08:00');
    });

    test('markTaken forwards to logDose with status="taken"', () async {
      String? capturedStatus;
      String? capturedScheduleId;
      DateTime? capturedSlot;

      final controller = MedicineController(
        fetchSchedules: () async => const [],
        createSchedule: (_) async => const MedicineApiResult(ok: false),
        deactivateSchedule: (_) async => false,
        logDose: ({
          required scheduleId,
          required scheduledFor,
          required status,
        }) async {
          capturedScheduleId = scheduleId;
          capturedSlot = scheduledFor;
          capturedStatus = status;
          return true;
        },
        reminderScheduler: InMemoryReminderScheduler(),
      );

      final slot = DateTime.utc(2026, 5, 20, 8);
      final ok = await controller.markTaken('s1', slot);
      expect(ok, isTrue);
      expect(capturedScheduleId, 's1');
      expect(capturedSlot, slot);
      expect(capturedStatus, 'taken');
    });

    test('deactivate cancels the local reminder for that schedule', () async {
      final scheduler = InMemoryReminderScheduler();
      var schedules = <MedicineSchedule>[
        const MedicineSchedule(
          id: 's1',
          name: 'Amlodipine',
          dose: '5 mg',
          frequency: 'once_daily',
          timesOfDay: ['08:00'],
          active: true,
        ),
      ];
      final controller = MedicineController(
        fetchSchedules: () async => schedules,
        createSchedule: (_) async => const MedicineApiResult(ok: false),
        deactivateSchedule: (scheduleId) async {
          schedules = [
            for (final s in schedules)
              if (s.id != scheduleId)
                s
              else
                MedicineSchedule(
                  id: s.id,
                  name: s.name,
                  dose: s.dose,
                  frequency: s.frequency,
                  timesOfDay: s.timesOfDay,
                  active: false,
                ),
          ];
          return true;
        },
        logDose: ({
          required scheduleId,
          required scheduledFor,
          required status,
        }) async => false,
        reminderScheduler: scheduler,
      );

      await controller.refresh();
      expect(scheduler.registered.length, 1);

      final ok = await controller.deactivate('s1');
      expect(ok, isTrue);
      // After deactivate + refresh, the inactive schedule contributes no
      // reminders even though the row is still in `schedules`.
      expect(scheduler.registered, isEmpty);
    });
  });
}

// Slice 14 — HomeShell live-tabs tests.
//
// Cover:
//   - LiveHomeTab fetches medicines and renders "I took it" / "Skip"
//   - Tapping a dose-action calls the controller and refreshes
//   - LiveRecordsTab lists records, opens signed link via launcher
//   - LiveSettingsTab renders consents + canonical Connected devices copy
//   - Toggling a consent calls updateConsent and re-reads state
//   - Sign out calls auth.logout and pops back to the splash

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/api_client.dart';
import 'package:med_emergency_mobile/auth/auth_repository.dart';
import 'package:med_emergency_mobile/auth/auth_storage.dart';
import 'package:med_emergency_mobile/connectivity.dart';
import 'package:med_emergency_mobile/home.dart';
import 'package:med_emergency_mobile/medicine.dart';
import 'package:med_emergency_mobile/profile_repository.dart';
import 'package:med_emergency_mobile/records_repository.dart';
import 'package:med_emergency_mobile/safety.dart';

ApiResponse _okBody(String raw) =>
    ApiResponse(statusCode: 200, body: raw, json: null);

ApiResponse _okJson(Map<String, dynamic> body) => ApiResponse(
      statusCode: 200,
      body: jsonEncode(body),
      json: body,
    );

ApiResponse _fail(int code) =>
    ApiResponse(statusCode: code, body: '', json: null);

void main() {
  group('LiveHomeTab', () {
    testWidgets('renders schedules and dose buttons', (tester) async {
      var taken = 0;
      final controller = MedicineController(
        fetchSchedules: () async => [
          const MedicineSchedule(
            id: 's-1',
            name: 'Amlodipine',
            dose: '5 mg',
            frequency: 'once_daily',
            timesOfDay: ['08:00'],
            active: true,
          ),
        ],
        createSchedule: (_) async =>
            const MedicineApiResult(ok: false),
        deactivateSchedule: (_) async => true,
        logDose: ({
          required String scheduleId,
          required DateTime scheduledFor,
          required String status,
        }) async {
          if (status == 'taken') taken += 1;
          return true;
        },
        reminderScheduler: InMemoryReminderScheduler(),
      );
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(
          connectivity: InMemoryConnectivityWatcher(),
          medicineController: controller,
        ),
      ));
      await tester.pumpAndSettle();
      expect(find.text('Amlodipine'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, 'I took it'));
      await tester.pumpAndSettle();
      expect(taken, 1);
    });

    testWidgets('empty state renders the canonical copy', (tester) async {
      final controller = MedicineController(
        fetchSchedules: () async => const [],
        createSchedule: (_) async => const MedicineApiResult(ok: false),
        deactivateSchedule: (_) async => true,
        logDose: ({
          required String scheduleId,
          required DateTime scheduledFor,
          required String status,
        }) async =>
            true,
        reminderScheduler: InMemoryReminderScheduler(),
      );
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(
          connectivity: InMemoryConnectivityWatcher(),
          medicineController: controller,
        ),
      ));
      await tester.pumpAndSettle();
      expect(find.text(emptyStateMedicines), findsOneWidget);
    });
  });

  group('LiveRecordsTab', () {
    testWidgets('lists records and opens signed link via launcher',
        (tester) async {
      Uri? opened;
      final repo = RecordsRepository(
        listRecordsCall: () async => _okBody(jsonEncode([
          {
            'id': 'r-1',
            'file_name': 'lab.pdf',
            'content_type': 'application/pdf',
            'record_type': 'lab',
            'tags': [],
            'size_bytes': 1024,
            'created_at': '2026-01-01T00:00:00Z',
          },
        ])),
        uploadRecordCall: ({
          required fileName,
          required contentType,
          required fileBytes,
          required formFields,
          required idempotencyKey,
        }) async =>
            _fail(500),
        linkRecordCall: (id) async => _okJson({
          'url': 'https://signed.example/$id.pdf',
          'expires_in_seconds': 900,
        }),
      );
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(
          connectivity: InMemoryConnectivityWatcher(),
          recordsRepository: repo,
          recordLinkLauncher: (uri) async {
            opened = uri;
            return true;
          },
        ),
      ));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Records'));
      await tester.pumpAndSettle();
      expect(find.text('lab.pdf'), findsOneWidget);
      await tester.tap(find.text('lab.pdf'));
      await tester.pumpAndSettle();
      expect(opened, isNotNull);
      expect(opened!.toString(), 'https://signed.example/r-1.pdf');
    });
  });

  group('LiveSettingsTab', () {
    testWidgets('renders consents + Connected devices and toggles update',
        (tester) async {
      var patched = 0;
      var currentDoctor = true;
      Future<ApiResponse> readConsents() async => _okBody(jsonEncode([
            {'consent_type': 'data_storage', 'granted': true},
            {
              'consent_type': 'emergency_share_with_doctor',
              'granted': currentDoctor,
            },
            {
              'consent_type': 'medicine_reminder_notifications',
              'granted': true,
            },
          ]));
      final repo = ProfileRepository(
        readProfileCall: () async => _okJson({
          'full_name': 'Demo Resident',
          'phone': '+15550009999',
          'flat_villa_number': 'A-101',
          'medical_profile': {'blood_group': 'O+'},
          'emergency_contacts': [
            {
              'name': 'Family',
              'phone': '+15550008888',
              'is_primary': true,
            },
          ],
        }),
        readConsentsCall: readConsents,
        updateConsentCall: (type, granted) async {
          patched += 1;
          if (type == 'emergency_share_with_doctor') currentDoctor = granted;
          return _okJson({'consent_type': type, 'granted': granted});
        },
      );
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(
          connectivity: InMemoryConnectivityWatcher(),
          profileRepository: repo,
        ),
      ));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      expect(find.text(connectedDevicesPhase2), findsOneWidget);
      expect(find.text('Demo Resident'), findsOneWidget);
      // Flip emergency_share_with_doctor off.
      final tile = find.ancestor(
        of: find.text('Share my profile with the on-duty doctor'),
        matching: find.byType(SwitchListTile),
      );
      await tester.tap(tile);
      await tester.pumpAndSettle();
      expect(patched, 1);
      expect(currentDoctor, isFalse);
    });

    testWidgets('Sign out calls auth.logout and pops back to splash',
        (tester) async {
      var loggedOut = false;
      final repo = ProfileRepository(
        readProfileCall: () async => _okJson({
          'full_name': 'Demo',
          'phone': '+15550009999',
          'flat_villa_number': 'A-101',
          'medical_profile': {},
          'emergency_contacts': [],
        }),
        readConsentsCall: () async => _okBody('[]'),
        updateConsentCall: (_, __) async => _fail(500),
      );
      final storage = InMemoryAuthStorage();
      await storage.saveSession(accessToken: 'a-1', refreshToken: 'r-1');
      final auth = AuthRepository(
        storage: storage,
        requestOtpCall: (_) async => _fail(500),
        verifyOtpCall: (_, __) async => _fail(500),
        refreshCall: (_) async => _fail(500),
        logoutCall: (_) async {
          loggedOut = true;
          return _okJson({});
        },
        meCall: () async => _fail(500),
      );
      await tester.pumpWidget(MaterialApp(
        home: Navigator(
          onGenerateRoute: (_) => MaterialPageRoute(
            builder: (_) => Scaffold(
              body: Builder(
                builder: (ctx) => Center(
                  child: ElevatedButton(
                    onPressed: () => Navigator.of(ctx).push(
                      MaterialPageRoute<void>(
                        builder: (_) => HomeShell(
                          connectivity: InMemoryConnectivityWatcher(),
                          profileRepository: repo,
                          authRepository: auth,
                        ),
                      ),
                    ),
                    child: const Text('OPEN'),
                  ),
                ),
              ),
            ),
          ),
        ),
      ));
      await tester.tap(find.text('OPEN'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sign out'));
      await tester.pumpAndSettle();
      expect(loggedOut, isTrue);
      expect(find.text('OPEN'), findsOneWidget,
          reason: 'sign out pops back to the root route');
    });
  });
}

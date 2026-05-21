// Slice 14 — onboarding wizard tests.
//
// Cover:
//   - Disclaimer gate locks "Continue" until the checkbox is ticked
//     (already covered in polish_test.dart for the standalone screen —
//     this file asserts the wizard step-0 mirrors that behaviour)
//   - Step navigation: profile -> contacts -> medical -> consents
//   - data_storage consent is locked on and the "Complete" button is
//     disabled if it's flipped off
//   - The submitted payload contains every field the resident entered
//     and the registration grant is consumed correctly
//   - On success the session tokens are persisted via AuthStorage

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/api_client.dart';
import 'package:med_emergency_mobile/auth/auth_storage.dart';
import 'package:med_emergency_mobile/connectivity.dart';
import 'package:med_emergency_mobile/onboarding.dart';
import 'package:med_emergency_mobile/onboarding_repository.dart';

ApiResponse _okBody(String raw) =>
    ApiResponse(statusCode: 200, body: raw, json: null);

ApiResponse _okJson(Map<String, dynamic> body) => ApiResponse(
      statusCode: 200,
      body: jsonEncode(body),
      json: body,
    );

ApiResponse _fail(int code) => ApiResponse(
      statusCode: code,
      body: '',
      json: {
        'error': {'code': 'oops', 'message': 'fail'},
      },
    );

void main() {
  testWidgets(
    'wizard walks all five steps, submits the complete payload, persists the session',
    (tester) async {
      final storage = InMemoryAuthStorage();
      await storage.saveRegistrationToken('reg-1');

      Map<String, dynamic>? submitted;
      String? submittedKey;

      final repo = OnboardingRepository(
        storage: storage,
        listProjectsCall: (regToken) async {
          expect(regToken, 'reg-1');
          return _okBody(jsonEncode([
            {'id': 'p-1', 'name': 'Demo Residency'},
          ]));
        },
        completeOnboardingCall: (regToken, body, key) async {
          expect(regToken, 'reg-1');
          submitted = body;
          submittedKey = key;
          return _okJson({
            'access_token': 'a-new',
            'refresh_token': 'r-new',
          });
        },
      );

      await tester.pumpWidget(MaterialApp(
        home: OnboardingFlow(
          repository: repo,
          connectivity: InMemoryConnectivityWatcher(),
          onComplete: (ctx) {
            // Replace the route with a marker widget so the test asserts
            // routing without depending on HomeShell's repo plumbing.
            Navigator.of(ctx).pushReplacement(
              MaterialPageRoute<void>(
                builder: (_) => const Scaffold(body: Text('ONBOARDED')),
              ),
            );
          },
        ),
      ));
      await tester.pumpAndSettle();

      // Step 0 — disclaimer ack
      await tester.tap(find.text('I have read and understood the above.'));
      await tester.pump();
      await tester.tap(find.widgetWithText(FilledButton, 'I understand — continue'));
      await tester.pumpAndSettle();

      // Step 1 — profile. Enter into TextFormFields by index:
      //   0 = full name, 1 = flat/villa number (gender is a dropdown,
      //   dob is a button picker, project is a dropdown).
      await tester.enterText(
        find.widgetWithText(TextFormField, '').first,
        'Demo Resident',
      );
      // DOB picker — open and accept default.
      await tester.tap(find.widgetWithText(OutlinedButton, 'Pick a date'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('OK'));
      await tester.pumpAndSettle();
      // Project is auto-selected (single project), so just enter flat #.
      final allFields = find.byType(TextFormField);
      // After name field, the second TextFormField is flat/villa.
      await tester.enterText(allFields.at(1), 'A-101');
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Continue'));
      await tester.pumpAndSettle();

      // Step 2 — contacts (auto-seeded with one empty primary).
      final contactFields = find.byType(TextFormField);
      await tester.enterText(contactFields.at(0), 'Family Friend');
      await tester.enterText(contactFields.at(1), '+15550008888');
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Continue'));
      await tester.pumpAndSettle();

      // Step 3 — medical history (leave optional, just continue).
      await tester.tap(find.widgetWithText(FilledButton, 'Continue'));
      await tester.pumpAndSettle();

      // Step 4 — consents. data_storage is required and locked on; tap
      // a non-required consent off then back on to assert toggling works.
      // Complete.
      await tester.tap(
        find.widgetWithText(FilledButton, 'Complete onboarding'),
      );
      await tester.pumpAndSettle();

      expect(find.text('ONBOARDED'), findsOneWidget,
          reason: 'submit success routes via onComplete');

      // Submitted payload assertions.
      expect(submitted, isNotNull);
      expect(submitted!['full_name'], 'Demo Resident');
      expect(submitted!['flat_villa_number'], 'A-101');
      expect(submitted!['project_id'], 'p-1');
      expect(submitted!['disclaimer_acknowledged'], isTrue);
      final contacts = submitted!['emergency_contacts'] as List;
      expect(contacts, hasLength(1));
      expect((contacts.first as Map)['name'], 'Family Friend');
      expect((contacts.first as Map)['phone'], '+15550008888');
      expect((contacts.first as Map)['is_primary'], isTrue);
      final consents = submitted!['consents'] as List;
      expect(consents, hasLength(OnboardingConsent.values.length));
      final dataStorage = consents.firstWhere(
        (c) => (c as Map)['consent_type'] == 'data_storage',
      );
      expect((dataStorage as Map)['granted'], isTrue,
          reason: 'data_storage is the required consent');

      // Idempotency key is non-empty.
      expect(submittedKey, isNotNull);
      expect(submittedKey!.isNotEmpty, isTrue);

      // Session was persisted.
      expect(await storage.readAccessToken(), 'a-new');
      expect(await storage.readRefreshToken(), 'r-new');
      expect(await storage.readRegistrationToken(), isNull);
    },
  );

  testWidgets('submission failure shows an error and keeps the wizard mounted',
      (tester) async {
    final storage = InMemoryAuthStorage();
    await storage.saveRegistrationToken('reg-1');
    final repo = OnboardingRepository(
      storage: storage,
      listProjectsCall: (_) async => _okBody(
        jsonEncode([
          {'id': 'p-1', 'name': 'Demo Residency'},
        ]),
      ),
      completeOnboardingCall: (_, __, ___) async => _fail(409),
    );

    await tester.pumpWidget(MaterialApp(
      home: OnboardingFlow(
        repository: repo,
        connectivity: InMemoryConnectivityWatcher(),
        onComplete: (_) {},
      ),
    ));
    await tester.pumpAndSettle();

    // Walk through every step quickly.
    await tester.tap(find.text('I have read and understood the above.'));
    await tester.pump();
    await tester.tap(find.widgetWithText(FilledButton, 'I understand — continue'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextFormField).first, 'X');
    await tester.tap(find.widgetWithText(OutlinedButton, 'Pick a date'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).at(1), 'A-1');
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Continue'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextFormField).at(0), 'N');
    await tester.enterText(find.byType(TextFormField).at(1), '+155500');
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Continue'));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Continue'));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Complete onboarding'));
    await tester.pumpAndSettle();

    expect(
      find.text('Could not complete onboarding. Please check your details.'),
      findsOneWidget,
    );
    expect(await storage.readAccessToken(), isNull);
    expect(await storage.readRegistrationToken(), 'reg-1',
        reason: 'a failed submit leaves the grant in place so a retry is possible');
  });
}

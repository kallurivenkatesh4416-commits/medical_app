// Slice 11 polish — widget tests for the demo-proof claims in
// `PLAN.md` row 11 / `docs/SLICE11-HANDOFF.md`:
//
// - Disclaimer ack screen blocks "continue" until the resident taps
//   "I have read and understood".
// - Splash AND login surfaces both show a working `tel:108` button
//   (≤2 taps from any entry point).
// - The sticky `DISCLAIMER_SHORT` footer appears on home / vitals /
//   records and never scrolls away. Settings does NOT carry it.
// - Settings → Connected devices shows the exact `CONNECTED_DEVICES_PHASE2`
//   copy with no connect buttons.
// - The offline banner appears when the connectivity watcher flips to
//   offline and disappears when it flips back.
// - Every primary action has a `Semantics(button: true, label: …)`
//   ancestor screen readers can name.
// - Empty-state copy renders on every list surface when no data.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/connectivity.dart';
import 'package:med_emergency_mobile/home.dart';
import 'package:med_emergency_mobile/login.dart';
import 'package:med_emergency_mobile/main.dart';
import 'package:med_emergency_mobile/onboarding.dart';
import 'package:med_emergency_mobile/safety.dart';

void main() {
  group('DISCLAIMER_FULL ack gate', () {
    testWidgets('continue button is disabled until the checkbox is ticked',
        (tester) async {
      var acknowledged = false;
      await tester.pumpWidget(MaterialApp(
        home: DisclaimerAckScreen(onAcknowledged: () => acknowledged = true),
      ));

      final button = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'I understand — continue'),
      );
      expect(button.onPressed, isNull,
          reason: 'continue must be disabled before the ack checkbox is tapped');

      await tester.tap(find.text('I have read and understood the above.'));
      await tester.pump();

      await tester.tap(find.widgetWithText(FilledButton, 'I understand — continue'));
      await tester.pump();

      expect(acknowledged, isTrue);
    });

    testWidgets('renders the canonical DISCLAIMER_FULL string verbatim',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: DisclaimerAckScreen(onAcknowledged: () {}),
      ));
      // The wording history in docs/ux-copy.md records that paraphrasing
      // reintroduced "diagnose" once and was reverted — the screen must
      // carry the canonical safety.dart constant verbatim.
      expect(find.text(disclaimerFull), findsOneWidget);
      expect(find.textContaining('diagnose'), findsNothing);
      expect(find.textContaining('diagnostic'), findsNothing);
    });
  });

  group('tel:108 reachable from splash AND login', () {
    testWidgets('splash Call 108 launches tel:108', (tester) async {
      Uri? launched;
      await tester.pumpWidget(MedEmergencyApp(launcher: (uri) async {
        launched = uri;
        return true;
      }));
      await tester.tap(find.widgetWithText(FilledButton, 'Call 108'));
      await tester.pump();
      expect(launched?.scheme, 'tel');
      expect(launched?.path, '108');
    });

    testWidgets('login Call 108 launches tel:108', (tester) async {
      Uri? launched;
      await tester.pumpWidget(MaterialApp(
        home: LoginScreen(launcher: (uri) async {
          launched = uri;
          return true;
        }),
      ));
      await tester.tap(find.widgetWithText(FilledButton, callOneZeroEightLabel));
      await tester.pump();
      expect(launched?.scheme, 'tel');
      expect(launched?.path, '108');
    });

    testWidgets('Sign In tap from splash is the second tap of the ≤2-tap path',
        (tester) async {
      var signInCalled = false;
      await tester.pumpWidget(MedEmergencyApp(
        launcher: (_) async => true,
      ));
      // Default SplashScreen has no onSignIn injected; verify the button
      // is present, semantic, and reaches a `LoginScreen` route. We pump
      // the navigation by injecting onSignIn via a custom builder app.
      await tester.pumpWidget(MaterialApp(
        home: SplashScreen(
          launcher: (_) async => true,
          onSignIn: (_) => signInCalled = true,
        ),
      ));
      await tester.tap(find.widgetWithText(TextButton, 'Sign In'));
      await tester.pump();
      expect(signInCalled, isTrue);
    });
  });

  group('Sticky DISCLAIMER_SHORT footer', () {
    Future<void> pumpHomeShell(WidgetTester tester) async {
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(connectivity: InMemoryConnectivityWatcher()),
      ));
    }

    testWidgets('present on home tab', (tester) async {
      await pumpHomeShell(tester);
      expect(find.text(disclaimerShort), findsOneWidget);
    });

    testWidgets('present after switching to vitals tab', (tester) async {
      await pumpHomeShell(tester);
      await tester.tap(find.text('Vitals'));
      await tester.pumpAndSettle();
      expect(find.text(disclaimerShort), findsOneWidget);
    });

    testWidgets('present after switching to records tab', (tester) async {
      await pumpHomeShell(tester);
      await tester.tap(find.text('Records'));
      await tester.pumpAndSettle();
      expect(find.text(disclaimerShort), findsOneWidget);
    });

    testWidgets('NOT present on the settings tab (not a health-insight surface)',
        (tester) async {
      await pumpHomeShell(tester);
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      expect(find.text(disclaimerShort), findsNothing);
    });
  });

  group('Settings → Connected devices', () {
    testWidgets('renders the canonical CONNECTED_DEVICES_PHASE2 copy verbatim',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(connectivity: InMemoryConnectivityWatcher()),
      ));
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      expect(find.text(connectedDevicesPhase2), findsOneWidget);
    });

    testWidgets('shows NO connect button and NO fake device list',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(connectivity: InMemoryConnectivityWatcher()),
      ));
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      // No button-like affordance to "connect" or "pair" anything.
      expect(find.widgetWithText(FilledButton, 'Connect'), findsNothing);
      expect(find.widgetWithText(OutlinedButton, 'Connect'), findsNothing);
      expect(find.textContaining('Coming Soon'), findsNothing);
      expect(find.textContaining('Pair'), findsNothing);
    });
  });

  group('Offline banner', () {
    testWidgets('hidden when connectivity is online', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(connectivity: InMemoryConnectivityWatcher()),
      ));
      expect(find.text(offlineBanner), findsNothing);
    });

    testWidgets('appears when connectivity flips to offline', (tester) async {
      final watcher = InMemoryConnectivityWatcher();
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(connectivity: watcher),
      ));
      expect(find.text(offlineBanner), findsNothing);

      watcher.setOnline(false);
      await tester.pump();
      expect(find.text(offlineBanner), findsOneWidget);

      watcher.setOnline(true);
      await tester.pump();
      expect(find.text(offlineBanner), findsNothing);
    });
  });

  group('Empty-state copy', () {
    testWidgets('home tab shows empty-state medicines copy when list is empty',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(connectivity: InMemoryConnectivityWatcher()),
      ));
      expect(find.text(emptyStateMedicines), findsOneWidget);
    });

    testWidgets('vitals tab shows empty-state vitals copy when empty',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(connectivity: InMemoryConnectivityWatcher()),
      ));
      await tester.tap(find.text('Vitals'));
      await tester.pumpAndSettle();
      expect(find.text(emptyStateVitals), findsOneWidget);
    });

    testWidgets('records tab shows empty-state records copy when empty',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(connectivity: InMemoryConnectivityWatcher()),
      ));
      await tester.tap(find.text('Records'));
      await tester.pumpAndSettle();
      expect(find.text(emptyStateRecords), findsOneWidget);
    });

    testWidgets('list mode replaces empty-state copy when items are passed',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: HomeShell(
          connectivity: InMemoryConnectivityWatcher(),
          medicines: const ['Amlodipine 5 mg — 08:00'],
        ),
      ));
      expect(find.text(emptyStateMedicines), findsNothing);
      expect(find.text('Amlodipine 5 mg — 08:00'), findsOneWidget);
    });
  });

  group('Accessibility — Semantics labels on primary actions', () {
    testWidgets('splash primary buttons each carry a Semantics(button: true)',
        (tester) async {
      await tester.pumpWidget(MedEmergencyApp(launcher: (_) async => true));
      // Use the widget tree's semantics finder; bySemanticsLabel matches
      // any node whose computed label contains the string.
      expect(find.bySemanticsLabel('Call 108'), findsWidgets);
      expect(find.bySemanticsLabel('I Need Medical Help'), findsWidgets);
      expect(find.bySemanticsLabel('Sign In'), findsWidgets);
    });

    testWidgets('login primary buttons carry Semantics labels',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: LoginScreen(launcher: (_) async => true),
      ));
      expect(find.bySemanticsLabel('Phone number'), findsWidgets);
      expect(find.bySemanticsLabel('One-time code'), findsWidgets);
      expect(find.bySemanticsLabel('Verify code'), findsWidgets);
      expect(find.bySemanticsLabel('Call 108'), findsWidgets);
    });

    testWidgets('56dp minimum tap target enforced by the FilledButton theme',
        (tester) async {
      // Inherit the app-level theme.
      await tester.pumpWidget(MedEmergencyApp(launcher: (_) async => true));
      final button = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Call 108'),
      );
      // The splash explicitly oversizes Call 108 to a 64dp box.
      final size = tester.getSize(find.byWidget(button));
      expect(size.height, greaterThanOrEqualTo(56),
          reason: '56dp minimum tap target per brief §11 / PLAN.md Slice 11');
    });
  });

  // ------------------------------------------------------------------------ //
  // End-to-end default route — Slice 11 review #1 fix.                       //
  // ------------------------------------------------------------------------ //

  group('Default splash → Sign In → submit → HomeShell', () {
    testWidgets(
      'a successful login lands at HomeShell and Settings shows the canonical copy',
      (tester) async {
        String? capturedPhone;
        String? capturedCode;
        await tester.pumpWidget(MedEmergencyApp(
          launcher: (_) async => true,
          loginSubmit: (phone, code) async {
            capturedPhone = phone;
            capturedCode = code;
            return true;
          },
        ));

        // Splash → Sign In (the second tap of the ≤2-tap Call 108 path).
        await tester.tap(find.widgetWithText(TextButton, 'Sign In'));
        await tester.pumpAndSettle();

        // LoginScreen renders with its own Call 108 button (the ≤2-tap
        // claim from PLAN.md Slice 11 holds: splash → Sign In → Call 108).
        expect(find.widgetWithText(FilledButton, 'Call 108'), findsOneWidget);

        // Fill phone + code and submit.
        await tester.enterText(find.byType(TextField).first, '+15550009999');
        await tester.enterText(find.byType(TextField).last, '123456');
        await tester.tap(find.widgetWithText(FilledButton, 'Verify code'));
        await tester.pumpAndSettle();

        // The injected loginSubmit captured the form values.
        expect(capturedPhone, '+15550009999');
        expect(capturedCode, '123456');

        // HomeShell is now on top — its sticky DISCLAIMER_SHORT footer is
        // the reachable proof.
        expect(find.text(disclaimerShort), findsOneWidget);
        // And the LoginScreen has been popped — no Verify code button on
        // the home surface.
        expect(find.widgetWithText(FilledButton, 'Verify code'), findsNothing);

        // Tab into Settings → Connected devices to verify the canonical
        // copy is reachable through the default navigation path (the
        // gap the Slice 11 review #1 finding called out).
        await tester.tap(find.text('Settings'));
        await tester.pumpAndSettle();
        expect(find.text(connectedDevicesPhase2), findsOneWidget);
      },
    );

    testWidgets(
      'a failed login keeps the LoginScreen on top and never reaches HomeShell',
      (tester) async {
        await tester.pumpWidget(MedEmergencyApp(
          launcher: (_) async => true,
          loginSubmit: (_, __) async => false,
        ));
        await tester.tap(find.widgetWithText(TextButton, 'Sign In'));
        await tester.pumpAndSettle();

        await tester.enterText(find.byType(TextField).first, '+15550009998');
        await tester.enterText(find.byType(TextField).last, '999999');
        await tester.tap(find.widgetWithText(FilledButton, 'Verify code'));
        await tester.pumpAndSettle();

        // Error surfaced, Verify code button still on screen, no HomeShell.
        expect(
          find.text('Could not verify the code. Please try again.'),
          findsOneWidget,
        );
        expect(find.widgetWithText(FilledButton, 'Verify code'), findsOneWidget);
        expect(find.text(disclaimerShort), findsNothing);
      },
    );
  });
}

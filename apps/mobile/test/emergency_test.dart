import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/emergency.dart';
import 'package:med_emergency_mobile/main.dart' show UriLauncher;

EmergencyController _controller({
  required AlertSender sender,
  AckChecker? ack,
  FallbackNumbersFetcher? numbers,
  FallbackRecorder? recorder,
  PendingAlertStore? store,
  String Function()? keyFactory,
  Duration retry = const Duration(milliseconds: 50),
  Duration ackWindow = const Duration(seconds: 30),
}) {
  return EmergencyController(
    sender: sender,
    ackChecker: ack ?? (_) async => false,
    numbersFetcher: numbers ?? (_) async => FallbackNumbers.offline,
    recorder: recorder ?? (_, __) async {},
    store: store,
    keyFactory: keyFactory ?? () => 'key-fixed',
    retryInterval: retry,
    ackWindow: ackWindow,
  );
}

Future<void> _pump(WidgetTester tester, EmergencyController c,
    {UriLauncher? launcher}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: EmergencyScreen(
        controller: c,
        launcher: launcher ?? (_) async => true,
      ),
    ),
  );
}

void main() {
  testWidgets('One tap sends the alert and shows "Alert sent"', (tester) async {
    var calls = 0;
    final c = _controller(sender: (_) async {
      calls++;
      return const AlertResult(ok: true, caseId: 'case-1');
    });

    await _pump(tester, c);
    await tester.tap(find.widgetWithText(FilledButton, 'I Need Medical Help'));
    await tester.pump();
    await tester.pump();

    expect(calls, 1);
    expect(find.textContaining('Alert sent'), findsOneWidget);
    c.dispose();
  });

  testWidgets('Failed send shows Retrying and keeps retrying in background',
      (tester) async {
    var calls = 0;
    final c = _controller(
      sender: (_) async {
        calls++;
        return const AlertResult(ok: false);
      },
    );

    await _pump(tester, c);
    await tester.tap(find.widgetWithText(FilledButton, 'I Need Medical Help'));
    await tester.pump();
    await tester.pump();
    expect(calls, 1);
    expect(find.textContaining('Retrying'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump();
    expect(calls, greaterThanOrEqualTo(2));
    c.dispose();
  });

  testWidgets('The same idempotency key is reused on every retry',
      (tester) async {
    final keys = <String>[];
    var calls = 0;
    final c = _controller(
      sender: (key) async {
        keys.add(key);
        calls++;
        return AlertResult(ok: calls >= 3, caseId: calls >= 3 ? 'case-k' : null);
      },
      keyFactory: () => 'tap-key-1',
    );

    await _pump(tester, c);
    await tester.tap(find.widgetWithText(FilledButton, 'I Need Medical Help'));
    await tester.pump();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump();

    expect(calls, greaterThanOrEqualTo(3));
    expect(keys.toSet(), {'tap-key-1'});
    c.dispose();
  });

  testWidgets('Restore resumes a persisted alert with the same key',
      (tester) async {
    final store = InMemoryPendingAlertStore();
    await store.save('persisted-key');
    final keys = <String>[];
    final c = _controller(
      sender: (key) async {
        keys.add(key);
        return const AlertResult(ok: true, caseId: 'case-r');
      },
      store: store,
      keyFactory: () => 'should-not-be-used',
    );

    await _pump(tester, c); // initState() calls restore()
    await tester.pump();
    await tester.pump();

    expect(keys, ['persisted-key']);
    expect(await store.load(), isNull); // cleared once confirmed
    c.dispose();
  });

  testWidgets('No ack within the window surfaces the fallback sheet',
      (tester) async {
    final c = _controller(
      sender: (_) async => const AlertResult(ok: true, caseId: 'case-9'),
      ack: (_) async => false,
      numbers: (_) async => const FallbackNumbers(
        emergency108: '108',
        emergency112: '112',
        doctor: '+15550009999',
        familyPrimary: '+15557770001',
        securityDesk: '+15550007777',
      ),
      ackWindow: const Duration(milliseconds: 50),
    );

    await _pump(tester, c);
    await tester.tap(find.widgetWithText(FilledButton, 'I Need Medical Help'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 60));
    await tester.pumpAndSettle();

    expect(find.text('Call doctor'), findsOneWidget);
    expect(find.text('Call 108'), findsOneWidget);
    expect(find.text('Call 112'), findsOneWidget);
    expect(find.text('Call primary family contact'), findsOneWidget);
    expect(find.text('Call security desk'), findsOneWidget);
    c.dispose();
  });

  testWidgets('Fallback tap records the channel and dials the number',
      (tester) async {
    Uri? launched;
    final recorded = <String>[];
    final c = _controller(
      sender: (_) async => const AlertResult(ok: true, caseId: 'case-7'),
      ack: (_) async => false,
      numbers: (_) async => const FallbackNumbers(
        emergency108: '108',
        emergency112: '112',
        doctor: '+15550001111',
      ),
      recorder: (caseId, channel) async => recorded.add('$caseId:$channel'),
      ackWindow: const Duration(milliseconds: 50),
    );

    await _pump(tester, c, launcher: (uri) async {
      launched = uri;
      return true;
    });
    await tester.tap(find.widgetWithText(FilledButton, 'I Need Medical Help'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 60));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Call doctor'));
    await tester.pumpAndSettle();

    expect(launched, isNotNull);
    expect(launched!.scheme, 'tel');
    expect(launched!.path, '+15550001111');
    expect(recorded, ['case-7:doctor']);
    c.dispose();
  });

  testWidgets('Security desk / family buttons hidden when not resolved',
      (tester) async {
    final c = _controller(
      sender: (_) async => const AlertResult(ok: true, caseId: 'case-3'),
      ack: (_) async => false,
      numbers: (_) async =>
          const FallbackNumbers(emergency108: '108', emergency112: '112'),
      ackWindow: const Duration(milliseconds: 50),
    );

    await _pump(tester, c);
    await tester.tap(find.widgetWithText(FilledButton, 'I Need Medical Help'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 60));
    await tester.pumpAndSettle();

    expect(find.text('Call 108'), findsOneWidget);
    expect(find.text('Call 112'), findsOneWidget);
    expect(find.text('Call security desk'), findsNothing);
    expect(find.text('Call primary family contact'), findsNothing);
    expect(find.text('Call doctor'), findsNothing);
    c.dispose();
  });
}

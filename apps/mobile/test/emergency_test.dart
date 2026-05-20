import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/emergency.dart';
import 'package:med_emergency_mobile/emergency_api.dart';
import 'package:med_emergency_mobile/main.dart' show UriLauncher;

EmergencyController _controller({
  required AlertSender sender,
  AckChecker? ack,
  FallbackNumbersFetcher? numbers,
  FallbackRecorder? recorder,
  PendingAlertStore? store,
  FallbackTapStore? fallbackStore,
  String Function()? keyFactory,
  String Function()? fallbackKeyFactory,
  Duration retry = const Duration(milliseconds: 50),
  Duration ackWindow = const Duration(seconds: 30),
}) {
  return EmergencyController(
    sender: sender,
    ackChecker: ack ?? (_) async => false,
    numbersFetcher: numbers ?? (_) async => FallbackNumbers.offline,
    recorder: recorder ?? (_, __, ___) async {},
    store: store,
    fallbackStore: fallbackStore,
    keyFactory: keyFactory ?? () => 'key-fixed',
    fallbackKeyFactory: fallbackKeyFactory,
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
  test('FileFallbackTapStore persists, replaces, and removes taps', () async {
    final file = File(
      '${Directory.systemTemp.path}/med_emergency_fallback_test_'
      '${DateTime.now().microsecondsSinceEpoch}.json',
    );
    final store = FileFallbackTapStore(file: file);
    addTearDown(() async {
      if (await file.exists()) await file.delete();
    });

    await store.save(
      const PendingFallbackTap(
        caseId: 'case-file',
        channel: FallbackChannelKey.emergency108,
        idempotencyKey: 'fb-file-1',
      ),
    );
    await store.save(
      const PendingFallbackTap(
        caseId: 'case-file',
        channel: FallbackChannelKey.emergency112,
        idempotencyKey: 'fb-file-1',
      ),
    );

    final saved = await store.loadAll();
    expect(saved, hasLength(1));
    expect(saved.single.channel, FallbackChannelKey.emergency112);

    await store.remove('fb-file-1');
    expect(await store.loadAll(), isEmpty);
    expect(await file.exists(), isFalse);
  });

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

  testWidgets('Restore resumes an unconfirmed alert with the same key',
      (tester) async {
    final store = InMemoryPendingAlertStore();
    await store.save(const PendingAlert(idempotencyKey: 'persisted-key'));
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
    // Confirmed: the case id is now persisted so a later kill can resume the
    // countdown/fallback (not cleared until acknowledged or a fallback tap).
    final saved = await store.load();
    expect(saved?.idempotencyKey, 'persisted-key');
    expect(saved?.caseId, 'case-r');
    c.dispose();
  });

  testWidgets('Restore of a CONFIRMED alert resumes fallback without re-sending',
      (tester) async {
    final store = InMemoryPendingAlertStore();
    await store.save(
      const PendingAlert(idempotencyKey: 'k', caseId: 'case-confirmed'),
    );
    var senderCalls = 0;
    final c = _controller(
      sender: (_) async {
        senderCalls++;
        return const AlertResult(ok: true, caseId: 'case-confirmed');
      },
      ack: (_) async => false,
      numbers: (_) async =>
          const FallbackNumbers(emergency108: '108', emergency112: '112'),
      store: store,
      ackWindow: const Duration(milliseconds: 50),
    );

    await _pump(tester, c); // initState() -> restore() (confirmed path)
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 60));
    await tester.pumpAndSettle();

    expect(senderCalls, 0); // the case already exists; must not re-send
    expect(find.text('Call 108'), findsOneWidget); // countdown still fired
    c.dispose();
  });

  testWidgets('Acknowledged within the window clears the persisted alert',
      (tester) async {
    final store = InMemoryPendingAlertStore();
    final c = _controller(
      sender: (_) async => const AlertResult(ok: true, caseId: 'case-ack'),
      ack: (_) async => true,
      store: store,
      ackWindow: const Duration(milliseconds: 50),
    );

    await _pump(tester, c);
    await tester.tap(find.widgetWithText(FilledButton, 'I Need Medical Help'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 60));
    await tester.pump();

    expect(await store.load(), isNull);
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
      recorder: (caseId, channel, _) async => recorded.add('$caseId:$channel'),
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

  testWidgets('Offline fallback tap is persisted and synced later',
      (tester) async {
    Uri? launched;
    var online = false;
    final recorded = <String>[];
    final tapStore = InMemoryFallbackTapStore();
    final c = _controller(
      sender: (_) async => const AlertResult(ok: true, caseId: 'case-offline-fb'),
      ack: (_) async => false,
      numbers: (_) async => const FallbackNumbers(
        emergency108: '108',
        emergency112: '112',
        doctor: '+15550002222',
      ),
      recorder: (caseId, channel, key) async {
        if (!online) throw StateError('offline');
        recorded.add('$caseId:$channel:$key');
      },
      fallbackStore: tapStore,
      fallbackKeyFactory: () => 'fb-key-1',
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

    expect(launched?.path, '+15550002222');
    expect(recorded, isEmpty);
    final pending = await tapStore.loadAll();
    expect(pending, hasLength(1));
    expect(pending.single.idempotencyKey, 'fb-key-1');

    online = true;
    await c.syncPendingFallbackTaps();

    expect(recorded, ['case-offline-fb:doctor:fb-key-1']);
    expect(await tapStore.loadAll(), isEmpty);
    c.dispose();
  });

  testWidgets('2G latency still reaches fallback without duplicate alert sends',
      (tester) async {
    var sends = 0;
    final c = _controller(
      sender: (_) async {
        sends++;
        await Future<void>.delayed(const Duration(milliseconds: 40));
        return const AlertResult(ok: true, caseId: 'case-2g');
      },
      ack: (_) async {
        await Future<void>.delayed(const Duration(milliseconds: 160));
        return false;
      },
      numbers: (_) async {
        await Future<void>.delayed(const Duration(milliseconds: 160));
        return const FallbackNumbers(
          emergency108: '108',
          emergency112: '112',
          doctor: '+15550003333',
        );
      },
      retry: const Duration(milliseconds: 50),
      ackWindow: const Duration(milliseconds: 50),
    );

    await _pump(tester, c);
    await tester.tap(find.widgetWithText(FilledButton, 'I Need Medical Help'));
    await tester.pump();
    expect(find.textContaining('Sending'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 45));
    await tester.pump();
    expect(find.textContaining('Alert sent'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 10));
    await tester.pump(const Duration(milliseconds: 170));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 170));
    await tester.pumpAndSettle();

    expect(sends, 1);
    expect(find.text('Call doctor'), findsOneWidget);
    expect(find.text('Call 108'), findsOneWidget);
    expect(find.text('Call 112'), findsOneWidget);
    c.dispose();
  });
}

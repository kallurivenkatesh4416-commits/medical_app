// Slice 16 — mobile FCM seam tests.
//
// Cover:
//   - DeviceTokenRepository.registerToken: maps 200 -> true, surfaces
//     non-2xx as false, and skips empty tokens without a network call.
//   - DeviceTokenRepository.acknowledgePush: same shape (200 -> true).
//   - SplashScreen AuthGate fires _registerPushTokenIfAvailable on a
//     restored resident session and threads through to the backend
//     `register` typedef with the token + platform.
//   - Skip behaviour: provider returning null is a hard no-op (no
//     /me/device-tokens call), so the Option-A default keeps the
//     resident path working without Firebase config files.

import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/api_client.dart';
import 'package:med_emergency_mobile/auth/auth_repository.dart';
import 'package:med_emergency_mobile/auth/auth_storage.dart';
import 'package:med_emergency_mobile/main.dart';
import 'package:med_emergency_mobile/push/device_token_repository.dart';
import 'package:med_emergency_mobile/push/push_token_provider.dart';
import 'package:med_emergency_mobile/safety.dart';

ApiResponse _ok([Map<String, dynamic>? body]) => ApiResponse(
      statusCode: 200,
      body: '',
      json: body,
    );

ApiResponse _fail(int code, [String error = 'oops']) => ApiResponse(
      statusCode: code,
      body: '',
      json: {
        'error': {'code': error, 'message': error},
      },
    );

void main() {
  group('DeviceTokenRepository.registerToken', () {
    test('200 from /me/device-tokens -> true', () async {
      String? capturedToken;
      String? capturedPlatform;
      final repo = DeviceTokenRepository(
        registerTokenCall: (token, platform) async {
          capturedToken = token;
          capturedPlatform = platform;
          return _ok({'registered': true});
        },
        acknowledgeCall: (_) async => _fail(500),
      );
      final ok = await repo.registerToken(token: 'fcm-1', platform: 'android');
      expect(ok, isTrue);
      expect(capturedToken, 'fcm-1');
      expect(capturedPlatform, 'android');
    });

    test('empty token never hits the network', () async {
      var called = false;
      final repo = DeviceTokenRepository(
        registerTokenCall: (token, platform) async {
          called = true;
          return _ok();
        },
        acknowledgeCall: (_) async => _fail(500),
      );
      final ok = await repo.registerToken(token: '', platform: 'android');
      expect(ok, isFalse);
      expect(called, isFalse,
          reason: 'an empty token must not be POSTed to the backend');
    });

    test('non-2xx surfaces as false without throwing', () async {
      final repo = DeviceTokenRepository(
        registerTokenCall: (_, __) async => _fail(403, 'forbidden'),
        acknowledgeCall: (_) async => _fail(500),
      );
      final ok = await repo.registerToken(token: 'fcm-2', platform: 'android');
      expect(ok, isFalse);
    });
  });

  group('DeviceTokenRepository.acknowledgePush', () {
    test('200 from /notifications/fcm/ack -> true', () async {
      String? captured;
      final repo = DeviceTokenRepository(
        registerTokenCall: (_, __) async => _fail(500),
        acknowledgeCall: (providerRef) async {
          captured = providerRef;
          return _ok({'status': 'delivered', 'case_id': 'c-1'});
        },
      );
      final ok = await repo.acknowledgePush('projects/demo/messages/abc');
      expect(ok, isTrue);
      expect(captured, 'projects/demo/messages/abc');
    });

    test('empty provider_ref is a hard no-op', () async {
      var called = false;
      final repo = DeviceTokenRepository(
        registerTokenCall: (_, __) async => _fail(500),
        acknowledgeCall: (_) async {
          called = true;
          return _ok();
        },
      );
      final ok = await repo.acknowledgePush('');
      expect(ok, isFalse);
      expect(called, isFalse);
    });

    test('404 surfaces as false (owner mismatch or unknown ref)', () async {
      final repo = DeviceTokenRepository(
        registerTokenCall: (_, __) async => _fail(500),
        acknowledgeCall: (_) async => _fail(404, 'notification_attempt_not_found'),
      );
      final ok = await repo.acknowledgePush('projects/demo/messages/ghost');
      expect(ok, isFalse);
    });
  });

  group('SplashScreen AuthGate -> post-restore push registration', () {
    AuthRepository residentSessionRepo() => AuthRepository(
          storage: InMemoryAuthStorage(),
          requestOtpCall: (_) async => _fail(500),
          verifyOtpCall: (_, __) async => _fail(500),
          refreshCall: (_) async => _fail(500),
          logoutCall: (_) async => _fail(500),
          meCall: () async => _ok({
            'id': 'u-1',
            'phone': '+15550009999',
            'role': 'resident',
            'full_name': 'Demo Resident',
          }),
        );

    testWidgets(
      'a non-null push token gets registered with the backend',
      (tester) async {
        String? capturedToken;
        String? capturedPlatform;
        final repo = DeviceTokenRepository(
          registerTokenCall: (token, platform) async {
            capturedToken = token;
            capturedPlatform = platform;
            return _ok({'registered': true});
          },
          acknowledgeCall: (_) async => _fail(500),
        );

        await tester.pumpWidget(MedEmergencyApp(
          launcher: (_) async => true,
          authRepository: residentSessionRepo(),
          deviceTokenRepository: repo,
          // Provider returns a token, so the AuthGate's post-restore
          // hook should POST it to the backend.
          pushTokenProvider: () async => 'fcm-test-token-A',
        ));
        await tester.pumpAndSettle();

        // The HomeShell is on top — sticky disclaimer is the marker.
        expect(find.text(disclaimerShort), findsOneWidget);
        expect(capturedToken, 'fcm-test-token-A');
        expect(capturedPlatform, defaultDevicePlatform());
      },
    );

    testWidgets(
      'a null push token is a hard no-op — Option A default stays silent',
      (tester) async {
        var registerCalled = false;
        final repo = DeviceTokenRepository(
          registerTokenCall: (_, __) async {
            registerCalled = true;
            return _ok();
          },
          acknowledgeCall: (_) async => _fail(500),
        );

        await tester.pumpWidget(MedEmergencyApp(
          launcher: (_) async => true,
          authRepository: residentSessionRepo(),
          deviceTokenRepository: repo,
          // The Option-A default — Firebase config files not present,
          // so no FCM token is available. The post-login flow must
          // skip the registration call entirely.
          pushTokenProvider: nullPushTokenProvider,
        ));
        await tester.pumpAndSettle();

        expect(find.text(disclaimerShort), findsOneWidget);
        expect(registerCalled, isFalse,
            reason:
                'Null token must not produce a /me/device-tokens call.');
      },
    );

    testWidgets(
      'a thrown provider does not crash the home handoff',
      (tester) async {
        final repo = DeviceTokenRepository(
          registerTokenCall: (_, __) async => _ok(),
          acknowledgeCall: (_) async => _fail(500),
        );
        await tester.pumpWidget(MedEmergencyApp(
          launcher: (_) async => true,
          authRepository: residentSessionRepo(),
          deviceTokenRepository: repo,
          pushTokenProvider: () async => throw Exception('FCM init failed'),
        ));
        await tester.pumpAndSettle();
        // HomeShell still reached — push registration failures are
        // best-effort, never blocking (Slice 6 fan-out invariant: SMS +
        // voice cover the missing channel).
        expect(find.text(disclaimerShort), findsOneWidget);
      },
    );
  });
}

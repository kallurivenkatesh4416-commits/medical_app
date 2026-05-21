// Slice 14 — auth flow tests.
//
// Cover:
//   - AuthStorage in-memory save/clear contract
//   - AuthRepository.requestOtp surfaces dev_otp and rate limits
//   - AuthRepository.verifyOtp routes login-vs-registration outcomes
//   - AuthRepository.refresh on success / on failure clears storage
//   - AuthRepository.logout clears storage even when network errors
//   - SplashScreen's AuthGate routes a resident bearer to HomeShell
//     and falls back to the public splash when /me returns null

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/api_client.dart';
import 'package:med_emergency_mobile/auth/auth_repository.dart';
import 'package:med_emergency_mobile/auth/auth_storage.dart';
import 'package:med_emergency_mobile/main.dart';
import 'package:med_emergency_mobile/safety.dart';

ApiResponse _ok(Map<String, dynamic> json) =>
    ApiResponse(statusCode: 200, body: json.toString(), json: json);

ApiResponse _fail(int code, [String error = 'oops']) => ApiResponse(
      statusCode: code,
      body: '',
      json: {
        'error': {'code': error, 'message': error},
      },
    );

void main() {
  group('InMemoryAuthStorage', () {
    test('saveSession stores access + refresh and clears registration grant',
        () async {
      final s = InMemoryAuthStorage();
      await s.saveRegistrationToken('reg-1');
      await s.saveSession(accessToken: 'a-1', refreshToken: 'r-1');
      expect(await s.readAccessToken(), 'a-1');
      expect(await s.readRefreshToken(), 'r-1');
      expect(await s.readRegistrationToken(), isNull,
          reason: 'login supersedes any in-flight registration grant');
    });

    test('clear empties every slot', () async {
      final s = InMemoryAuthStorage();
      await s.saveSession(accessToken: 'a', refreshToken: 'r');
      await s.saveRegistrationToken('g');
      await s.clear();
      expect(await s.readAccessToken(), isNull);
      expect(await s.readRefreshToken(), isNull);
      expect(await s.readRegistrationToken(), isNull);
    });
  });

  group('AuthRepository.requestOtp', () {
    test('returns dev_otp when backend exposes it (APP_ENV=local)', () async {
      final repo = AuthRepository(
        storage: InMemoryAuthStorage(),
        requestOtpCall: (phone) async => _ok({'sent': true, 'dev_otp': '123456'}),
        verifyOtpCall: (_, __) async => _fail(500),
        refreshCall: (_) async => _fail(500),
        logoutCall: (_) async => _fail(500),
        meCall: () async => _fail(500),
      );
      final r = await repo.requestOtp('+15550009999');
      expect(r.ok, isTrue);
      expect(r.devOtp, '123456');
    });

    test('surfaces a server error code for the UI to translate', () async {
      final repo = AuthRepository(
        storage: InMemoryAuthStorage(),
        requestOtpCall: (_) async => _fail(429, 'rate_limited'),
        verifyOtpCall: (_, __) async => _fail(500),
        refreshCall: (_) async => _fail(500),
        logoutCall: (_) async => _fail(500),
        meCall: () async => _fail(500),
      );
      final r = await repo.requestOtp('+15550009999');
      expect(r.ok, isFalse);
      expect(r.errorCode, 'rate_limited');
    });
  });

  group('AuthRepository.verifyOtp', () {
    test('login outcome persists access + refresh', () async {
      final storage = InMemoryAuthStorage();
      final repo = AuthRepository(
        storage: storage,
        requestOtpCall: (_) async => _fail(500),
        verifyOtpCall: (_, __) async => _ok({
          'access_token': 'a-1',
          'refresh_token': 'r-1',
          'registration_required': false,
        }),
        refreshCall: (_) async => _fail(500),
        logoutCall: (_) async => _fail(500),
        meCall: () async => _fail(500),
      );
      final out = await repo.verifyOtp('+15550009999', '123456');
      expect(out.ok, isTrue);
      expect(out.loggedIn, isTrue);
      expect(out.registrationRequired, isFalse);
      expect(await storage.readAccessToken(), 'a-1');
      expect(await storage.readRefreshToken(), 'r-1');
    });

    test('registration outcome persists the grant only', () async {
      final storage = InMemoryAuthStorage();
      final repo = AuthRepository(
        storage: storage,
        requestOtpCall: (_) async => _fail(500),
        verifyOtpCall: (_, __) async => _ok({
          'registration_required': true,
          'registration_token': 'g-1',
        }),
        refreshCall: (_) async => _fail(500),
        logoutCall: (_) async => _fail(500),
        meCall: () async => _fail(500),
      );
      final out = await repo.verifyOtp('+15550009999', '123456');
      expect(out.ok, isTrue);
      expect(out.loggedIn, isFalse);
      expect(out.registrationRequired, isTrue);
      expect(await storage.readRegistrationToken(), 'g-1');
      expect(await storage.readAccessToken(), isNull,
          reason: 'no session is minted on the registration branch');
    });

    test('non-2xx leaves storage untouched and surfaces the error code',
        () async {
      final storage = InMemoryAuthStorage();
      final repo = AuthRepository(
        storage: storage,
        requestOtpCall: (_) async => _fail(500),
        verifyOtpCall: (_, __) async => _fail(401, 'otp_invalid'),
        refreshCall: (_) async => _fail(500),
        logoutCall: (_) async => _fail(500),
        meCall: () async => _fail(500),
      );
      final out = await repo.verifyOtp('+15550009999', '123456');
      expect(out.ok, isFalse);
      expect(out.errorCode, 'otp_invalid');
      expect(await storage.readAccessToken(), isNull);
    });
  });

  group('AuthRepository.refresh', () {
    test('success rotates the pair', () async {
      final storage = InMemoryAuthStorage();
      await storage.saveSession(accessToken: 'a-1', refreshToken: 'r-1');
      final repo = AuthRepository(
        storage: storage,
        requestOtpCall: (_) async => _fail(500),
        verifyOtpCall: (_, __) async => _fail(500),
        refreshCall: (raw) async {
          expect(raw, 'r-1');
          return _ok({'access_token': 'a-2', 'refresh_token': 'r-2'});
        },
        logoutCall: (_) async => _fail(500),
        meCall: () async => _fail(500),
      );
      final ok = await repo.refresh();
      expect(ok, isTrue);
      expect(await storage.readAccessToken(), 'a-2');
      expect(await storage.readRefreshToken(), 'r-2');
    });

    test('failure clears the session so the next launch routes to splash',
        () async {
      final storage = InMemoryAuthStorage();
      await storage.saveSession(accessToken: 'a', refreshToken: 'r');
      final repo = AuthRepository(
        storage: storage,
        requestOtpCall: (_) async => _fail(500),
        verifyOtpCall: (_, __) async => _fail(500),
        refreshCall: (_) async => _fail(401, 'refresh_reused'),
        logoutCall: (_) async => _fail(500),
        meCall: () async => _fail(500),
      );
      final ok = await repo.refresh();
      expect(ok, isFalse);
      expect(await storage.readAccessToken(), isNull);
      expect(await storage.readRefreshToken(), isNull);
    });

    test('no stored refresh token returns false without a network call',
        () async {
      var called = false;
      final repo = AuthRepository(
        storage: InMemoryAuthStorage(),
        requestOtpCall: (_) async => _fail(500),
        verifyOtpCall: (_, __) async => _fail(500),
        refreshCall: (_) async {
          called = true;
          return _fail(500);
        },
        logoutCall: (_) async => _fail(500),
        meCall: () async => _fail(500),
      );
      final ok = await repo.refresh();
      expect(ok, isFalse);
      expect(called, isFalse);
    });
  });

  group('AuthRepository.logout', () {
    test('clears storage even when the server call throws', () async {
      final storage = InMemoryAuthStorage();
      await storage.saveSession(accessToken: 'a', refreshToken: 'r');
      final repo = AuthRepository(
        storage: storage,
        requestOtpCall: (_) async => _fail(500),
        verifyOtpCall: (_, __) async => _fail(500),
        refreshCall: (_) async => _fail(500),
        logoutCall: (_) async => throw Exception('network down'),
        meCall: () async => _fail(500),
      );
      await repo.logout();
      expect(await storage.readAccessToken(), isNull);
      expect(await storage.readRefreshToken(), isNull);
    });
  });

  group('SplashScreen — AuthGate routing', () {
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

    AuthRepository noSessionRepo() => AuthRepository(
          storage: InMemoryAuthStorage(),
          requestOtpCall: (_) async => _fail(500),
          verifyOtpCall: (_, __) async => _fail(500),
          refreshCall: (_) async => _fail(500),
          logoutCall: (_) async => _fail(500),
          meCall: () async => _fail(401),
        );

    testWidgets('resident session routes straight to HomeShell',
        (tester) async {
      await tester.pumpWidget(MedEmergencyApp(
        launcher: (_) async => true,
        authRepository: residentSessionRepo(),
      ));
      // The bootstrap kicks off a postFrameCallback — flush microtasks twice
      // (post-frame -> me() -> pushReplacement -> HomeShell first paint).
      await tester.pumpAndSettle();
      expect(find.text(disclaimerShort), findsOneWidget,
          reason: 'HomeShell carries the sticky DISCLAIMER_SHORT footer');
      expect(find.widgetWithText(FilledButton, 'Call 108'), findsNothing,
          reason: 'the public splash is no longer on top after AuthGate routes');
    });

    testWidgets('no stored session falls back to the public splash',
        (tester) async {
      await tester.pumpWidget(MedEmergencyApp(
        launcher: (_) async => true,
        authRepository: noSessionRepo(),
      ));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(FilledButton, 'Call 108'), findsOneWidget,
          reason: 'public splash should be on top so emergency entry stays reachable');
      expect(find.widgetWithText(TextButton, 'Sign In'), findsOneWidget);
    });
  });
}

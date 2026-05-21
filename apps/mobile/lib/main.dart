import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:url_launcher/url_launcher.dart';

import 'api_client.dart';
import 'auth/auth_repository.dart';
import 'auth/auth_storage.dart';
import 'connectivity.dart';
import 'emergency.dart';
import 'emergency_api.dart';
import 'home.dart';
import 'login.dart';
import 'medicine.dart';
import 'medicines_repository.dart';
import 'onboarding.dart';
import 'onboarding_repository.dart';
import 'profile_repository.dart';
import 'push/device_token_repository.dart';
import 'push/push_token_provider.dart';
import 'records_repository.dart';
import 'records_upload_picker.dart';
import 'safety.dart';

/// i18n placeholder (brief §11): every user-facing string is wrapped so
/// Telugu and Hindi can land in Phase 2 by swapping this implementation.
/// `tr` stays the identity function in Slice 14 — the discipline is the
/// wrapping, not the resolution.
String tr(String key) => key;

/// The only hardcoded fallback number (brief §2.2). Reachable offline.
final Uri kCall108 = Uri(scheme: 'tel', path: '108');

/// Default API base URL. Override at run time with
/// `--dart-define=API_BASE_URL=https://staging.example`.
const String kDefaultApiBase = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://10.0.2.2:8000',
);

/// Injectable so widget tests can verify the call without a real dialer.
typedef UriLauncher = Future<bool> Function(Uri uri);

Future<bool> defaultLauncher(Uri uri) =>
    launchUrl(uri, mode: LaunchMode.externalApplication);

/// Slice 14 entry point: bootstraps the production wiring (secure storage,
/// API client, auth repository, connectivity, path_provider-backed emergency
/// outbox) before the first frame. Widget tests bypass `main` entirely —
/// they construct [MedEmergencyApp] directly with in-memory seams.
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final dir = await _resolveAppPrivateDir();
  final storage = SecureFlutterAuthStorage();
  final api = ApiClient(baseUrl: kDefaultApiBase, storage: storage);
  final authRepo = buildAuthRepository(api);
  final onboardingRepo = buildOnboardingRepository(api);
  final profileRepo = buildProfileRepository(api);
  final recordsRepo = buildRecordsRepository(api);
  final deviceTokenRepo = buildDeviceTokenRepository(api);
  final reminderScheduler = InMemoryReminderScheduler();
  final connectivity = ConnectivityPlusWatcher();
  // Production-only: registers the file_picker-backed upload helper for
  // the LiveRecordsTab FAB. Widget tests never call `main`, so the seam
  // stays null and the FAB no-ops on the test path.
  registerRecordsPickerUploader();
  runApp(MedEmergencyApp(
    appPrivateDir: dir,
    apiBaseUrl: kDefaultApiBase,
    authStorage: storage,
    authRepository: authRepo,
    onboardingRepository: onboardingRepo,
    profileRepository: profileRepo,
    recordsRepository: recordsRepo,
    deviceTokenRepository: deviceTokenRepo,
    // Slice 16 — Option A wiring: the seam returns null today. A future
    // micro-slice swaps `nullPushTokenProvider` for a
    // `FirebaseMessaging.instance.getToken`-backed adapter once the
    // Firebase project / config files are provisioned. The post-login
    // registration step skips when this returns null, so the resident
    // path stays fully functional with no Firebase wired.
    pushTokenProvider: nullPushTokenProvider,
    medicineControllerBuilder: () =>
        buildMedicineController(api, reminderScheduler: reminderScheduler),
    emergencyControllerBuilder: () => EmergencyApi(
      baseUrl: kDefaultApiBase,
      tokenProvider: storage.readAccessToken,
      appPrivateDir: dir,
    ).buildController(),
    connectivity: connectivity,
  ));
}

Future<Directory> _resolveAppPrivateDir() async {
  try {
    return await getApplicationSupportDirectory();
  } catch (_) {
    // No platform channel binding (e.g. Linux desktop dev). Fall back to
    // the OS temp dir; the durability tradeoff is named in
    // docs/open-questions.md.
    return Directory.systemTemp;
  }
}

class MedEmergencyApp extends StatelessWidget {
  const MedEmergencyApp({
    super.key,
    this.launcher = defaultLauncher,
    this.emergencyControllerBuilder,
    this.connectivity,
    this.loginSubmit,
    this.appPrivateDir,
    this.apiBaseUrl,
    this.authStorage,
    this.authRepository,
    this.onboardingRepository,
    this.profileRepository,
    this.recordsRepository,
    this.medicineControllerBuilder,
    this.deviceTokenRepository,
    this.pushTokenProvider,
  });

  final UriLauncher launcher;

  /// Injected by widget tests; the app default builds a controller backed
  /// by the real (`dart:io`) [EmergencyApi].
  final EmergencyController Function()? emergencyControllerBuilder;

  /// Optional connectivity watcher for the offline banner. Defaults to an
  /// always-online in-memory stub; production wires [ConnectivityPlusWatcher].
  final ConnectivityWatcher? connectivity;

  /// Slice 14 — widget tests can still inject a single closure to fake the
  /// `phone+code -> bool` verify path without building an [AuthRepository].
  /// Ignored when [authRepository] is provided.
  final Future<bool> Function(String phone, String code)? loginSubmit;

  /// App-private directory for the emergency outbox files. Null in widget
  /// tests; production resolves it via `path_provider` in `main`.
  final Directory? appPrivateDir;

  /// Base URL the resident-path repositories call against. Null in widget
  /// tests (every typedef is faked) and falls back to [kDefaultApiBase].
  final String? apiBaseUrl;

  /// JWT storage. Provided in production by `main`; tests can pass an
  /// `InMemoryAuthStorage` to exercise the AuthGate without Keystore.
  final AuthStorage? authStorage;

  /// When provided the splash first checks for a live session and routes
  /// directly to `HomeShell`. Tests leave this null so the legacy splash
  /// → sign-in → home path stays intact.
  final AuthRepository? authRepository;

  /// Onboarding HTTP surface (registration-grant gated). When null the
  /// "registration required" outcome on login surfaces an inline error
  /// instead of routing to the wizard — used by widget tests that exercise
  /// only the login surface.
  final OnboardingRepository? onboardingRepository;

  /// Slice 14 — resident-self repos threaded through HomeShell when the
  /// user is authenticated. All three are null in widget tests; the
  /// existing constructor-driven HomeTab/RecordsTab/SettingsTab render the
  /// legacy stub data with no HTTP calls.
  final ProfileRepository? profileRepository;
  final RecordsRepository? recordsRepository;
  final MedicineController Function()? medicineControllerBuilder;

  /// Slice 16 — push delivery seam. The repo wraps the
  /// `/me/device-tokens` and `/notifications/fcm/ack` endpoints; the
  /// provider returns the current FCM token (today: `nullPushTokenProvider`
  /// — see `push/push_token_provider.dart` for the Option-A deferred
  /// wiring). Production calls the pair after a successful login /
  /// onboarding to register the resident's device with the backend.
  final DeviceTokenRepository? deviceTokenRepository;
  final PushTokenProvider? pushTokenProvider;

  @override
  Widget build(BuildContext context) {
    // Brief §11 elderly-UX defaults: 18sp body, 22sp emphasis, primary
    // buttons reach the 56dp minimum tap target. Applied at the theme
    // level so every screen (splash / login / onboarding / home / vitals
    // / records / settings) inherits without each surface re-declaring.
    final theme = ThemeData(
      useMaterial3: true,
      textTheme: const TextTheme(
        bodyLarge: TextStyle(fontSize: 18),
        bodyMedium: TextStyle(fontSize: 16),
        labelLarge: TextStyle(fontSize: 22),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(56, 56),
          textStyle: const TextStyle(fontSize: 22),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(56, 56),
          textStyle: const TextStyle(fontSize: 18),
        ),
      ),
    );
    final effectiveConnectivity = connectivity ?? InMemoryConnectivityWatcher();
    return MaterialApp(
      title: 'Emergency Health',
      theme: theme,
      home: SplashScreen(
        launcher: launcher,
        emergencyControllerBuilder: emergencyControllerBuilder ??
            () => EmergencyApi(appPrivateDir: appPrivateDir).buildController(),
        connectivity: effectiveConnectivity,
        loginSubmit: loginSubmit,
        authRepository: authRepository,
        onboardingRepository: onboardingRepository,
        profileRepository: profileRepository,
        recordsRepository: recordsRepository,
        medicineControllerBuilder: medicineControllerBuilder,
        deviceTokenRepository: deviceTokenRepository,
        pushTokenProvider: pushTokenProvider,
      ),
    );
  }
}

/// Splash carries a working offline "Call 108" action even when not logged in
/// (brief §2.2 / §14). For this emergency fallback we launch directly (no
/// canLaunchUrl gate that could silently no-op) and surface any failure
/// visibly so the user is never left with a dead button.
///
/// Slice 14: when an [authRepository] is provided, the splash first probes
/// `me()` and pushReplacements to `HomeShell` if the stored session is
/// still valid. While that check is in flight a lightweight loader keeps
/// the screen non-blank. Tests do not provide an [authRepository], so the
/// legacy public splash renders immediately as before.
class SplashScreen extends StatefulWidget {
  const SplashScreen({
    super.key,
    this.launcher = defaultLauncher,
    this.emergencyControllerBuilder,
    this.connectivity,
    this.onSignIn,
    this.loginSubmit,
    this.authRepository,
    this.onboardingRepository,
    this.profileRepository,
    this.recordsRepository,
    this.medicineControllerBuilder,
    this.deviceTokenRepository,
    this.pushTokenProvider,
  });

  final UriLauncher launcher;
  final EmergencyController Function()? emergencyControllerBuilder;
  final ConnectivityWatcher? connectivity;

  /// Injected by widget tests so the Sign-in tap can route to a custom
  /// destination (e.g. the [LoginScreen] with a fake `onSubmit`) without
  /// the real login flow needing to be reachable through a Navigator
  /// stack. Production defaults to pushing the real [LoginScreen].
  final void Function(BuildContext context)? onSignIn;

  /// Threaded through to [LoginScreen.onSubmit] so the end-to-end
  /// splash → login → home path is exercisable without HTTP.
  final Future<bool> Function(String phone, String code)? loginSubmit;

  /// Slice 14 — when provided, the splash first attempts to restore the
  /// resident's session via `GET /auth/me`. Null in widget tests.
  final AuthRepository? authRepository;

  /// Slice 14 — passed through to the LoginScreen so the verify response's
  /// `registration_required: true` branch can push the onboarding wizard.
  final OnboardingRepository? onboardingRepository;

  /// Slice 14 — threaded into HomeShell at first construction (on
  /// successful login or restored session) so the resident tabs render
  /// real data. All three are null on the test path.
  final ProfileRepository? profileRepository;
  final RecordsRepository? recordsRepository;
  final MedicineController Function()? medicineControllerBuilder;

  /// Slice 16 — push delivery seam. Production wires the
  /// `DeviceTokenRepository` + a real `PushTokenProvider`; the
  /// post-login flow calls them once a token is available.
  final DeviceTokenRepository? deviceTokenRepository;
  final PushTokenProvider? pushTokenProvider;

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  bool _bootstrapping = false;

  @override
  void initState() {
    super.initState();
    final repo = widget.authRepository;
    if (repo != null) {
      _bootstrapping = true;
      WidgetsBinding.instance.addPostFrameCallback((_) => _restoreSession(repo));
    }
  }

  Future<void> _restoreSession(AuthRepository repo) async {
    final user = await repo.me();
    if (!mounted) return;
    final effectiveConnectivity =
        widget.connectivity ?? InMemoryConnectivityWatcher();
    if (user != null && user.role == 'resident') {
      // Best-effort device-token registration: fire-and-forget so a
      // failed registration never blocks the home-screen handoff. The
      // backend already considers SMS + voice as fallbacks for any
      // missing FCM channel (Slice 6 fan-out invariant).
      unawaited(_registerPushTokenIfAvailable());
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => HomeShell(
            connectivity: effectiveConnectivity,
            profileRepository: widget.profileRepository,
            recordsRepository: widget.recordsRepository,
            medicineController: widget.medicineControllerBuilder?.call(),
            authRepository: widget.authRepository,
          ),
        ),
      );
      return;
    }
    // No stored session, expired tokens, or a non-resident role hitting the
    // resident app — render the public splash so emergency entry stays
    // reachable. The legacy splash content takes over on the next frame.
    setState(() => _bootstrapping = false);
  }

  /// Slice 16: best-effort post-login push token registration. Pulls the
  /// current FCM token from [PushTokenProvider]; when it's null (the
  /// Option-A default until Firebase config files arrive), this method
  /// becomes a no-op. Errors are swallowed — a failed registration is
  /// not a blocker because the Slice 6 fan-out still has SMS + voice as
  /// fallbacks for any missing FCM channel.
  Future<void> _registerPushTokenIfAvailable() async {
    final repo = widget.deviceTokenRepository;
    final provider = widget.pushTokenProvider;
    if (repo == null || provider == null) return;
    try {
      final token = await provider();
      if (token == null || token.isEmpty) return;
      await repo.registerToken(
        token: token,
        platform: defaultDevicePlatform(),
      );
    } catch (_) {
      // Intentionally swallow — see method docstring.
    }
  }

  void _openEmergency(BuildContext context) {
    final builder = widget.emergencyControllerBuilder;
    if (builder == null) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => EmergencyScreen(
          controller: builder(),
          launcher: widget.launcher,
        ),
      ),
    );
  }

  void _openSignIn(BuildContext context) {
    if (widget.onSignIn != null) {
      widget.onSignIn!(context);
      return;
    }
    final effectiveConnectivity =
        widget.connectivity ?? InMemoryConnectivityWatcher();
    final repo = widget.authRepository;
    final onboardingRepo = widget.onboardingRepository;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => LoginScreen(
          launcher: widget.launcher,
          // Production: when [authRepository] is provided the LoginScreen
          // wires its own two-step OTP flow; the legacy `loginSubmit` hook
          // is only used by widget tests that bypass the real repo.
          onSubmit: widget.loginSubmit,
          authRepository: repo,
          onAuthenticated: (loginContext) {
            unawaited(_registerPushTokenIfAvailable());
            Navigator.of(loginContext).pushReplacement(
              MaterialPageRoute<void>(
                builder: (_) => HomeShell(
                  connectivity: effectiveConnectivity,
                  profileRepository: widget.profileRepository,
                  recordsRepository: widget.recordsRepository,
                  medicineController: widget.medicineControllerBuilder?.call(),
                  authRepository: widget.authRepository,
                ),
              ),
            );
          },
          onRegistrationRequired: onboardingRepo == null
              ? null
              : (loginContext, _) {
                  Navigator.of(loginContext).pushReplacement(
                    MaterialPageRoute<void>(
                      builder: (_) => OnboardingFlow(
                        repository: onboardingRepo,
                        connectivity: effectiveConnectivity,
                        onComplete: (onboardCtx) {
                          unawaited(_registerPushTokenIfAvailable());
                          Navigator.of(onboardCtx).pushReplacement(
                            MaterialPageRoute<void>(
                              builder: (_) => HomeShell(
                                connectivity: effectiveConnectivity,
                                profileRepository: widget.profileRepository,
                                recordsRepository: widget.recordsRepository,
                                medicineController:
                                    widget.medicineControllerBuilder?.call(),
                                authRepository: widget.authRepository,
                              ),
                            ),
                          );
                        },
                      ),
                    ),
                  );
                },
        ),
      ),
    );
  }

  Future<void> _call108(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    var ok = false;
    try {
      ok = await widget.launcher(kCall108);
    } catch (_) {
      ok = false;
    }
    if (!ok) {
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            tr('Could not open the dialer. Please dial 108 directly.'),
          ),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_bootstrapping) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                tr('Emergency Health'),
                style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Text(tr(disclaimerShort), textAlign: TextAlign.center),
              ),
              const SizedBox(height: 32),
              Semantics(
                button: true,
                label: tr('Call 108'),
                child: SizedBox(
                  width: double.infinity,
                  height: 64,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    child: FilledButton(
                      style: FilledButton.styleFrom(
                        backgroundColor: Colors.red,
                        textStyle: const TextStyle(fontSize: 22),
                      ),
                      onPressed: () => _call108(context),
                      child: Text(tr('Call 108')),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Semantics(
                button: true,
                label: tr('I Need Medical Help'),
                child: SizedBox(
                  width: double.infinity,
                  height: 64,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    child: OutlinedButton(
                      onPressed: () => _openEmergency(context),
                      child: Text(tr('I Need Medical Help')),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              // PLAN.md Slice 11: Sign In is the second tap that takes the
              // resident to the login surface, which carries its own
              // `Call 108` button. Splash → Sign In → Call 108 is the 2-tap
              // path the demo proof asserts.
              Semantics(
                button: true,
                label: tr('Sign In'),
                child: SizedBox(
                  width: double.infinity,
                  height: 56,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    child: TextButton(
                      onPressed: () => _openSignIn(context),
                      child: Text(tr('Sign In')),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

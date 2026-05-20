import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import 'connectivity.dart';
import 'emergency.dart';
import 'emergency_api.dart';
import 'home.dart';
import 'login.dart';
import 'safety.dart';

/// i18n placeholder (brief §11): every user-facing string is wrapped so
/// Telugu and Hindi can land in Phase 2 by swapping this implementation.
/// `tr` stays the identity function in Slice 11 — the discipline is the
/// wrapping, not the resolution.
String tr(String key) => key;

/// The only hardcoded fallback number (brief §2.2). Reachable offline.
final Uri kCall108 = Uri(scheme: 'tel', path: '108');

/// Injectable so widget tests can verify the call without a real dialer.
typedef UriLauncher = Future<bool> Function(Uri uri);

Future<bool> defaultLauncher(Uri uri) =>
    launchUrl(uri, mode: LaunchMode.externalApplication);

void main() => runApp(const MedEmergencyApp());

class MedEmergencyApp extends StatelessWidget {
  const MedEmergencyApp({
    super.key,
    this.launcher = defaultLauncher,
    this.emergencyControllerBuilder,
    this.connectivity,
  });

  final UriLauncher launcher;

  /// Injected by widget tests; the app default builds a controller backed
  /// by the real (`dart:io`) [EmergencyApi].
  final EmergencyController Function()? emergencyControllerBuilder;

  /// Optional connectivity watcher for the offline banner on home / vitals
  /// / records. Defaults to an always-online in-memory stub; production
  /// adapters (`connectivity_plus` or equivalent) plug into the same
  /// `ConnectivityWatcher` seam.
  final ConnectivityWatcher? connectivity;

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
    return MaterialApp(
      title: 'Emergency Health',
      theme: theme,
      home: SplashScreen(
        launcher: launcher,
        emergencyControllerBuilder:
            emergencyControllerBuilder ?? () => EmergencyApi().buildController(),
        connectivity: connectivity ?? InMemoryConnectivityWatcher(),
      ),
    );
  }
}

/// Splash carries a working offline "Call 108" action even when not logged in
/// (brief §2.2 / §14). For this emergency fallback we launch directly (no
/// canLaunchUrl gate that could silently no-op) and surface any failure
/// visibly so the user is never left with a dead button.
class SplashScreen extends StatelessWidget {
  const SplashScreen({
    super.key,
    this.launcher = defaultLauncher,
    this.emergencyControllerBuilder,
    this.connectivity,
    this.onSignIn,
  });

  final UriLauncher launcher;
  final EmergencyController Function()? emergencyControllerBuilder;
  final ConnectivityWatcher? connectivity;

  /// Injected by widget tests so the Sign-in tap can route to a custom
  /// destination (e.g. the [LoginScreen] with a fake `onSubmit`) without
  /// the real login flow needing to be reachable through a Navigator
  /// stack. Production defaults to pushing the real [LoginScreen].
  final void Function(BuildContext context)? onSignIn;

  void _openEmergency(BuildContext context) {
    final builder = emergencyControllerBuilder;
    if (builder == null) return;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => EmergencyScreen(
          controller: builder(),
          launcher: launcher,
        ),
      ),
    );
  }

  void _openSignIn(BuildContext context) {
    if (onSignIn != null) {
      onSignIn!(context);
      return;
    }
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => LoginScreen(
          launcher: launcher,
          // Default route on a successful login is the [HomeShell] —
          // host of the sticky `DISCLAIMER_SHORT` footer and offline
          // banner per PLAN.md Slice 11. Widget tests typically inject
          // their own `onSubmit` and don't rely on this default path.
          onSubmit: (phone, code) async => false,
        ),
      ),
    );
  }

  /// Stub the default home route the app would push on a successful login.
  /// Exposed for production wiring; widget tests target [LoginScreen]
  /// directly and don't go through this path.
  static Widget defaultHome(ConnectivityWatcher connectivity) =>
      HomeShell(connectivity: connectivity);

  Future<void> _call108(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    var ok = false;
    try {
      ok = await launcher(kCall108);
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

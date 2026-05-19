import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import 'emergency.dart';
import 'emergency_api.dart';

/// i18n placeholder (brief §11): every user-facing string is wrapped so Telugu
/// and Hindi can be added in Phase 2. Real implementation lands in Slice 11.
String tr(String key) => key;

/// Brief §2.1 disclaimer — exact required wording. No diagnosis language.
const String disclaimerShort =
    'This app does not replace emergency hospital care. '
    'In a life-threatening situation, call 108 / 112 immediately.';

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
  });

  final UriLauncher launcher;

  /// Injected by widget tests; the app default builds a controller backed by
  /// the real (`dart:io`) [EmergencyApi].
  final EmergencyController Function()? emergencyControllerBuilder;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Emergency Health',
      theme: ThemeData(
        useMaterial3: true,
        // Elderly-friendly defaults (brief §11). Refined in Slice 11.
        textTheme: const TextTheme(
          bodyLarge: TextStyle(fontSize: 18),
          labelLarge: TextStyle(fontSize: 22),
        ),
      ),
      home: SplashScreen(
        launcher: launcher,
        emergencyControllerBuilder:
            emergencyControllerBuilder ?? () => EmergencyApi().buildController(),
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
  });

  final UriLauncher launcher;
  final EmergencyController Function()? emergencyControllerBuilder;

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
            ],
          ),
        ),
      ),
    );
  }
}

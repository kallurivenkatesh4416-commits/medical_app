import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

/// i18n placeholder (brief §11): every user-facing string is wrapped so Telugu
/// and Hindi can be added in Phase 2. Real implementation lands in Slice 11.
String tr(String key) => key;

/// Brief §2.1 disclaimer — exact required wording. No diagnosis language.
const String disclaimerShort =
    'This app does not replace emergency hospital care. '
    'In a life-threatening situation, call 108 / 112 immediately.';

/// The only hardcoded fallback number (brief §2.2). Reachable offline.
final Uri kCall108 = Uri(scheme: 'tel', path: '108');

void main() => runApp(const MedEmergencyApp());

class MedEmergencyApp extends StatelessWidget {
  const MedEmergencyApp({super.key});

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
      home: const SplashScreen(),
    );
  }
}

/// Splash carries a working offline "Call 108" action even when not logged in
/// (brief §2.2 / §14): one tap places the call via the native dialer.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  Future<void> _call108() async {
    if (await canLaunchUrl(kCall108)) {
      await launchUrl(kCall108);
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
                      onPressed: _call108,
                      child: Text(tr('Call 108')),
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

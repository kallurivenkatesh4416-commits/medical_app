import 'package:flutter/material.dart';

/// i18n placeholder (brief §11): every user-facing string is wrapped so Telugu
/// and Hindi can be added in Phase 2. Real implementation lands in Slice 11.
String tr(String key) => key;

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

/// Splash carries the offline "Call 108" action even when not logged in
/// (brief §2.2 / §14). Wired to a real tel: deep link in Slice 11.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

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
              Text(tr('Not a diagnostic tool. Call 108 in a life-threatening emergency.')),
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
                      onPressed: () {}, // tel:108 deep link — Slice 11
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

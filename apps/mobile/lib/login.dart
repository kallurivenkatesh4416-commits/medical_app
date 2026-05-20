/// Slice 11 login surface.
///
/// Carries the **same `Call 108` button as the splash** (brief §2.2 +
/// PLAN.md Slice 11: `tel:108` reachable in ≤2 taps from any entry
/// point). Reuses the injectable `UriLauncher` typedef from `main.dart`
/// so the splash and login dialer assertions share one fixture.
///
/// The OTP flow itself is intentionally a thin shim — Slice 2 already
/// shipped the backend OTP/JWT contract. Slice 11 surfaces it for the
/// resident with the same elderly-UX defaults as the splash: 56dp tap
/// targets, 22sp emphasis, 18sp body, Semantics labels, `tr()` wrapping,
/// and the offline emergency button always visible.

library;

import 'package:flutter/material.dart';

import 'main.dart' show UriLauncher, defaultLauncher, kCall108, tr;
import 'safety.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    this.launcher = defaultLauncher,
    this.onSubmit,
    this.onAuthenticated,
  });

  final UriLauncher launcher;

  /// Optional submit hook the widget tests inject to verify the OTP
  /// happy-path without hitting HTTP. Returns true on success. Production
  /// wiring is the real OTP API call — left as a Phase-2 task.
  final Future<bool> Function(String phone, String code)? onSubmit;

  /// Called from inside the submit handler with the build context **after**
  /// [onSubmit] returns true. The splash wires this to push the
  /// `HomeShell` route (Slice 11 — the disclaimer-short footer and
  /// offline banner only become reachable through this transition).
  /// Without this callback the screen has no success path; the Slice 11
  /// review #1 fix wires it.
  final void Function(BuildContext context)? onAuthenticated;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _code = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _phone.dispose();
    _code.dispose();
    super.dispose();
  }

  Future<void> _call108() async {
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

  Future<void> _submit() async {
    if (widget.onSubmit == null) {
      setState(() {
        _error = tr('Login is not wired in this build.');
      });
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final ok = await widget.onSubmit!(_phone.text.trim(), _code.text.trim());
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = ok ? null : tr('Could not verify the code. Please try again.');
    });
    // Slice 11 review #1 fix: a successful login MUST hand off to the
    // next route. Without this the HomeShell + sticky disclaimer + offline
    // banner + Settings → Connected devices surfaces are only reachable
    // through tests that pump HomeShell directly.
    if (ok && widget.onAuthenticated != null) {
      widget.onAuthenticated!(context);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                tr('Sign In'),
                style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              Text(
                tr('Use your registered phone number to receive a one-time code.'),
                style: const TextStyle(fontSize: 16),
              ),
              const SizedBox(height: 18),
              Semantics(
                textField: true,
                label: tr('Phone number'),
                child: TextField(
                  controller: _phone,
                  keyboardType: TextInputType.phone,
                  style: const TextStyle(fontSize: 18),
                  decoration: InputDecoration(
                    labelText: tr('Phone number'),
                    border: const OutlineInputBorder(),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Semantics(
                textField: true,
                label: tr('One-time code'),
                child: TextField(
                  controller: _code,
                  keyboardType: TextInputType.number,
                  style: const TextStyle(fontSize: 18),
                  decoration: InputDecoration(
                    labelText: tr('One-time code'),
                    border: const OutlineInputBorder(),
                  ),
                ),
              ),
              if (_error != null) ...[
                const SizedBox(height: 8),
                Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 14)),
              ],
              const SizedBox(height: 16),
              Semantics(
                button: true,
                label: tr('Verify code'),
                child: SizedBox(
                  height: 56,
                  child: FilledButton(
                    onPressed: _busy ? null : _submit,
                    child: Text(
                      _busy ? tr('Verifying…') : tr('Verify code'),
                      style: const TextStyle(fontSize: 18),
                    ),
                  ),
                ),
              ),
              const Spacer(),
              // The same offline `Call 108` button the splash carries —
              // ≤2 taps from any entry point per brief §2.2 / PLAN.md
              // Slice 11.
              Semantics(
                button: true,
                label: tr(callOneZeroEightLabel),
                child: SizedBox(
                  height: 64,
                  child: FilledButton(
                    style: FilledButton.styleFrom(
                      backgroundColor: Colors.red,
                      textStyle: const TextStyle(fontSize: 22),
                    ),
                    onPressed: _call108,
                    child: Text(tr(callOneZeroEightLabel)),
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

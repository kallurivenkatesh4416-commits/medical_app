/// Slice 11 + 14 login surface.
///
/// Carries the **same `Call 108` button as the splash** (brief §2.2 +
/// PLAN.md Slice 11: `tel:108` reachable in ≤2 taps from any entry
/// point). Reuses the injectable `UriLauncher` typedef from `main.dart`
/// so the splash and login dialer assertions share one fixture.
///
/// Slice 14 wires the screen to the real two-step OTP flow:
///
/// 1. Resident enters phone → "Send code" → `POST /api/v1/auth/otp/request`
///    (a dev-mode backend returns the OTP in the response so the demo can
///    auto-fill it).
/// 2. Resident enters the code → "Verify code" → `POST /api/v1/auth/otp/verify`
///    branches three ways:
///       a) `access_token + refresh_token` → tokens persisted, route to
///          `HomeShell` via `onAuthenticated`.
///       b) `registration_required: true` → registration grant persisted,
///          route to the onboarding flow via `onRegistrationRequired`.
///       c) anything else → error message; the screen stays mounted.
///
/// Widget tests inject the legacy `onSubmit` (single-callback `phone, code
/// -> bool`) instead of an [AuthRepository] so the existing 19-test
/// baseline keeps running with no HTTP mocks. When both are provided the
/// repository wins.

library;

import 'package:flutter/material.dart';

import 'auth/auth_repository.dart';
import 'main.dart' show UriLauncher, defaultLauncher, kCall108, tr;
import 'safety.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    this.launcher = defaultLauncher,
    this.onSubmit,
    this.onAuthenticated,
    this.authRepository,
    this.onRegistrationRequired,
  });

  final UriLauncher launcher;

  /// Legacy test hook — `(phone, code) -> bool`. Used only when
  /// [authRepository] is null. Production builds always provide a
  /// repository; the existing widget tests keep using this seam.
  final Future<bool> Function(String phone, String code)? onSubmit;

  /// Called from inside the submit handler with the build context **after**
  /// authentication succeeds (existing account, tokens persisted). The
  /// splash wires this to push the `HomeShell` route.
  final void Function(BuildContext context)? onAuthenticated;

  /// Slice 14 — when provided the screen drives a two-step OTP flow against
  /// the backend. When null, the screen falls back to [onSubmit] for tests.
  final AuthRepository? authRepository;

  /// Slice 14 — invoked when `/auth/otp/verify` returns
  /// `registration_required: true`. The splash wires this to push the
  /// onboarding flow. Null is acceptable in tests (the registration path
  /// then surfaces an inline error so the failure is visible, not silent).
  final void Function(BuildContext context, String phone)? onRegistrationRequired;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _code = TextEditingController();
  String? _error;
  String? _info;
  bool _busyVerify = false;
  bool _busyRequest = false;
  bool _codeRequested = false;

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

  Future<void> _sendCode() async {
    final repo = widget.authRepository;
    if (repo == null) return;
    final phone = _phone.text.trim();
    if (phone.isEmpty) {
      setState(() => _error = tr('Enter your phone number first.'));
      return;
    }
    setState(() {
      _busyRequest = true;
      _error = null;
      _info = null;
    });
    final result = await repo.requestOtp(phone);
    if (!mounted) return;
    setState(() {
      _busyRequest = false;
      _codeRequested = result.ok;
    });
    if (!result.ok) {
      setState(() => _error = _otpRequestErrorFor(result.errorCode));
      return;
    }
    // Dev-mode convenience: the backend returns the OTP only when
    // APP_ENV=local. In production this field is null and the resident
    // reads the code from their SMS.
    final dev = result.devOtp;
    if (dev != null && dev.isNotEmpty) {
      _code.text = dev;
      setState(() => _info = tr('Dev OTP auto-filled. Tap Verify code.'));
    } else {
      setState(() => _info = tr('Code sent. Check your SMS.'));
    }
  }

  Future<void> _submit() async {
    final repo = widget.authRepository;
    final phone = _phone.text.trim();
    final code = _code.text.trim();
    setState(() {
      _busyVerify = true;
      _error = null;
      _info = null;
    });
    final ok = await _verify(repo, phone, code);
    if (!mounted) return;
    setState(() {
      _busyVerify = false;
    });
    if (ok.routed) return; // navigation already happened
    if (!ok.success) {
      setState(() => _error = tr('Could not verify the code. Please try again.'));
    }
  }

  /// Routes the verify outcome through whichever seam is wired. The
  /// returned tuple reports whether the screen has already pushed a new
  /// route (so the caller does not try to surface a duplicate error) and
  /// whether the call itself succeeded.
  Future<_VerifyResult> _verify(
    AuthRepository? repo,
    String phone,
    String code,
  ) async {
    if (repo != null) {
      final outcome = await repo.verifyOtp(phone, code);
      if (!outcome.ok) return const _VerifyResult(success: false);
      if (outcome.registrationRequired) {
        final cb = widget.onRegistrationRequired;
        if (cb != null && mounted) {
          cb(context, phone);
          return const _VerifyResult(success: true, routed: true);
        }
        // No route wired — surface the gap so the failure is visible
        // rather than appearing as a silent "verify code didn't work".
        setState(() => _error = tr(
              'Account not found. Onboarding is not wired in this build.',
            ));
        return const _VerifyResult(success: true, routed: true);
      }
      // Existing-account login.
      if (outcome.loggedIn && widget.onAuthenticated != null && mounted) {
        widget.onAuthenticated!(context);
        return const _VerifyResult(success: true, routed: true);
      }
      return _VerifyResult(success: outcome.loggedIn);
    }
    // Legacy widget-test seam.
    if (widget.onSubmit == null) {
      setState(() {
        _error = tr('Login is not wired in this build.');
      });
      return const _VerifyResult(success: false);
    }
    final ok = await widget.onSubmit!(phone, code);
    if (ok && widget.onAuthenticated != null && mounted) {
      widget.onAuthenticated!(context);
      return const _VerifyResult(success: true, routed: true);
    }
    return _VerifyResult(success: ok);
  }

  String _otpRequestErrorFor(String? code) {
    if (code == null) return tr('Could not send a code. Please try again.');
    // Backend OTP errors come back as opaque `code` strings. Map the
    // ones the user can act on; everything else falls back to a generic
    // copy so we never leak server detail to the UI.
    switch (code) {
      case 'rate_limited':
      case 'otp_rate_limited':
        return tr('Too many attempts. Please wait a minute and try again.');
      case 'invalid_phone':
        return tr('That phone number is not in a valid format.');
      default:
        return tr('Could not send a code. Please try again.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final showSendCode = widget.authRepository != null;
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
              if (showSendCode) ...[
                const SizedBox(height: 10),
                Semantics(
                  button: true,
                  label: tr('Send code'),
                  child: SizedBox(
                    height: 48,
                    child: OutlinedButton(
                      onPressed: _busyRequest ? null : _sendCode,
                      child: Text(
                        _busyRequest ? tr('Sending…') : tr('Send code'),
                        style: const TextStyle(fontSize: 16),
                      ),
                    ),
                  ),
                ),
              ],
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
                    helperText: _codeRequested
                        ? tr('Code sent to your phone.')
                        : null,
                  ),
                ),
              ),
              if (_info != null) ...[
                const SizedBox(height: 8),
                Text(_info!,
                    style: const TextStyle(color: Color(0xFF34404F), fontSize: 14)),
              ],
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
                    onPressed: _busyVerify ? null : _submit,
                    child: Text(
                      _busyVerify ? tr('Verifying…') : tr('Verify code'),
                      style: const TextStyle(fontSize: 18),
                    ),
                  ),
                ),
              ),
              const Spacer(),
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

class _VerifyResult {
  const _VerifyResult({required this.success, this.routed = false});
  final bool success;
  final bool routed;
}

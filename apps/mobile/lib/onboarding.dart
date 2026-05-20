/// Slice 11 onboarding flow — focused on the **disclaimer acknowledgment
/// gate** that PLAN.md Slice 11 calls out as a required screen.
///
/// The acknowledgment is **not** a consent row — it's an audit-only event
/// (`AuditAction.DISCLAIMER_ACKNOWLEDGED`, [enums.py:168]). The backend
/// onboarding service already rejects `disclaimer_acknowledged=False` and
/// writes the audit row inside the same atomic transaction as the user /
/// profile / contacts / consents inserts ([onboarding_service.py:67]).
/// Slice 11's job is to surface this as a dedicated screen the resident
/// cannot bypass, not a buried checkbox.
///
/// The screen carries the **literal `DISCLAIMER_FULL`** text from
/// `safety.dart` — the wording history in `docs/ux-copy.md` records that
/// paraphrasing reintroduced "diagnose" once and was reverted.

library;

import 'package:flutter/material.dart';

import 'main.dart' show tr;
import 'safety.dart';

class DisclaimerAckScreen extends StatefulWidget {
  const DisclaimerAckScreen({
    super.key,
    required this.onAcknowledged,
  });

  /// Called when the resident taps the "I understand" button. The page
  /// owner forwards this to the onboarding submission so the
  /// `disclaimer_acknowledged: true` flag rides in the same atomic
  /// payload.
  final VoidCallback onAcknowledged;

  @override
  State<DisclaimerAckScreen> createState() => _DisclaimerAckScreenState();
}

class _DisclaimerAckScreenState extends State<DisclaimerAckScreen> {
  bool _readAndUnderstood = false;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(tr('Important Safety Notice'))),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                tr('Please read this before continuing'),
                style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 16),
              Expanded(
                child: SingleChildScrollView(
                  child: Text(
                    tr(disclaimerFull),
                    style: const TextStyle(fontSize: 18, height: 1.4),
                  ),
                ),
              ),
              const Divider(height: 32),
              Semantics(
                label: tr('Confirm I have read and understood the notice'),
                child: CheckboxListTile(
                  value: _readAndUnderstood,
                  controlAffinity: ListTileControlAffinity.leading,
                  contentPadding: EdgeInsets.zero,
                  title: Text(
                    tr('I have read and understood the above.'),
                    style: const TextStyle(fontSize: 18),
                  ),
                  onChanged: (value) => setState(
                    () => _readAndUnderstood = value ?? false,
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Semantics(
                button: true,
                label: tr('Continue to onboarding'),
                child: SizedBox(
                  height: 56,
                  child: FilledButton(
                    style: FilledButton.styleFrom(
                      textStyle: const TextStyle(fontSize: 22),
                    ),
                    onPressed: _readAndUnderstood ? widget.onAcknowledged : null,
                    child: Text(tr('I understand — continue')),
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

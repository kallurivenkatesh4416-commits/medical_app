import 'dart:async';

import 'package:flutter/material.dart';

import 'main.dart' show UriLauncher, defaultLauncher, tr;

/// Backend-resolved fallback numbers (PLAN.md Slice 6). Only 108/112 are
/// constants — every other number comes from project settings / on-call.
class FallbackNumbers {
  const FallbackNumbers({
    required this.emergency108,
    required this.emergency112,
    this.doctor,
    this.familyPrimary,
    this.securityDesk,
  });

  final String emergency108;
  final String emergency112;
  final String? doctor;
  final String? familyPrimary;
  final String? securityDesk;

  /// Offline-safe minimum: the national numbers are always reachable even
  /// when the backend cannot be reached to resolve the rest.
  static const FallbackNumbers offline =
      FallbackNumbers(emergency108: '108', emergency112: '112');
}

class AlertResult {
  const AlertResult({required this.ok, this.caseId});

  final bool ok;
  final String? caseId;
}

/// Injectable seams so widget tests run without a network or dialer.
typedef AlertSender = Future<AlertResult> Function();
typedef AckChecker = Future<bool> Function(String caseId);
typedef FallbackNumbersFetcher = Future<FallbackNumbers> Function(String caseId);
typedef FallbackRecorder = Future<void> Function(String caseId, String channel);

/// Channel keys — must match backend `FallbackChannel` (shared enum source).
class FallbackChannelKey {
  static const doctor = 'doctor';
  static const emergency108 = 'emergency_108';
  static const emergency112 = 'emergency_112';
  static const familyPrimary = 'family_primary';
  static const securityDesk = 'security_desk';
}

enum AlertPhase { idle, sending, sent, retrying }

/// Owns the alert lifecycle: send → offline retry every [retryInterval] →
/// a [ackWindow] countdown that, on no server acknowledgment, surfaces the
/// fallback sheet. The retry loop keeps running in the background; the
/// countdown never cancels it (PLAN.md Slice 6).
class EmergencyController extends ChangeNotifier {
  EmergencyController({
    required this.sender,
    required this.ackChecker,
    required this.numbersFetcher,
    required this.recorder,
    this.retryInterval = const Duration(seconds: 5),
    this.ackWindow = const Duration(seconds: 60),
  });

  final AlertSender sender;
  final AckChecker ackChecker;
  final FallbackNumbersFetcher numbersFetcher;
  final FallbackRecorder recorder;
  final Duration retryInterval;
  final Duration ackWindow;

  AlertPhase phase = AlertPhase.idle;
  bool fallbackVisible = false;
  String? caseId;

  Timer? _retryTimer;
  Timer? _ackTimer;
  bool _disposed = false;

  Future<void> trigger() async {
    if (phase == AlertPhase.sending || phase == AlertPhase.retrying) return;
    phase = AlertPhase.sending;
    fallbackVisible = false;
    _safeNotify();

    // The 60s countdown starts at the tap regardless of send success.
    _ackTimer?.cancel();
    _ackTimer = Timer(ackWindow, _onAckWindowElapsed);

    await _attemptSend();
  }

  Future<void> _attemptSend() async {
    AlertResult result;
    try {
      result = await sender();
    } catch (_) {
      result = const AlertResult(ok: false);
    }
    if (_disposed) return;

    if (result.ok) {
      caseId = result.caseId;
      phase = AlertPhase.sent;
      _retryTimer?.cancel();
      _retryTimer = null;
    } else {
      phase = AlertPhase.retrying;
      _retryTimer ??= Timer.periodic(retryInterval, (_) => _attemptSend());
    }
    _safeNotify();
  }

  Future<void> _onAckWindowElapsed() async {
    var acknowledged = false;
    if (caseId != null) {
      try {
        acknowledged = await ackChecker(caseId!);
      } catch (_) {
        acknowledged = false;
      }
    }
    if (_disposed) return;
    if (!acknowledged) {
      fallbackVisible = true;
      _safeNotify();
    }
  }

  Future<FallbackNumbers> loadFallbackNumbers() async {
    final id = caseId;
    if (id == null) return FallbackNumbers.offline;
    try {
      return await numbersFetcher(id);
    } catch (_) {
      return FallbackNumbers.offline;
    }
  }

  /// Record the chosen channel (best-effort — a failure must never stop the
  /// dial) then return so the caller can launch the dialer.
  Future<void> recordFallback(String channel) async {
    final id = caseId;
    if (id == null) return;
    try {
      await recorder(id, channel);
    } catch (_) {
      // Recording is best-effort; the call itself must still go through.
    }
  }

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _retryTimer?.cancel();
    _ackTimer?.cancel();
    super.dispose();
  }
}

class EmergencyScreen extends StatefulWidget {
  const EmergencyScreen({
    super.key,
    required this.controller,
    this.launcher = defaultLauncher,
  });

  final EmergencyController controller;
  final UriLauncher launcher;

  @override
  State<EmergencyScreen> createState() => _EmergencyScreenState();
}

class _EmergencyScreenState extends State<EmergencyScreen> {
  bool _sheetShown = false;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onChange);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onChange);
    super.dispose();
  }

  void _onChange() {
    if (!mounted) return;
    setState(() {});
    if (widget.controller.fallbackVisible && !_sheetShown) {
      _sheetShown = true;
      WidgetsBinding.instance.addPostFrameCallback((_) => _showFallbackSheet());
    }
  }

  String _statusText(AlertPhase phase) {
    switch (phase) {
      case AlertPhase.idle:
        return tr('Tap the button if you need medical help.');
      case AlertPhase.sending:
        return tr('Sending alert…');
      case AlertPhase.sent:
        return tr('Alert sent. Help is being notified.');
      case AlertPhase.retrying:
        return tr('No connection. Retrying… (alert will keep trying)');
    }
  }

  Future<void> _dial(String channel, String number) async {
    final messenger = ScaffoldMessenger.of(context);
    await widget.controller.recordFallback(channel);
    var ok = false;
    try {
      ok = await widget.launcher(Uri(scheme: 'tel', path: number));
    } catch (_) {
      ok = false;
    }
    if (!ok) {
      messenger.showSnackBar(
        SnackBar(content: Text(tr('Could not open the dialer. Please dial $number.'))),
      );
    }
  }

  Future<void> _showFallbackSheet() async {
    final numbers = await widget.controller.loadFallbackNumbers();
    if (!mounted) return;
    await showModalBottomSheet<void>(
      context: context,
      isDismissible: false,
      enableDrag: false,
      builder: (sheetContext) {
        final tiles = <Widget>[
          if (numbers.doctor != null)
            _FallbackButton(
              label: tr('Call doctor'),
              onPressed: () => _dial(FallbackChannelKey.doctor, numbers.doctor!),
            ),
          _FallbackButton(
            label: tr('Call 108'),
            onPressed: () =>
                _dial(FallbackChannelKey.emergency108, numbers.emergency108),
          ),
          _FallbackButton(
            label: tr('Call 112'),
            onPressed: () =>
                _dial(FallbackChannelKey.emergency112, numbers.emergency112),
          ),
          if (numbers.familyPrimary != null)
            _FallbackButton(
              label: tr('Call primary family contact'),
              onPressed: () =>
                  _dial(FallbackChannelKey.familyPrimary, numbers.familyPrimary!),
            ),
          if (numbers.securityDesk != null)
            _FallbackButton(
              label: tr('Call security desk'),
              onPressed: () =>
                  _dial(FallbackChannelKey.securityDesk, numbers.securityDesk!),
            ),
        ];
        return SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  tr('No response yet. Reach help directly:'),
                  style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 16),
                for (final t in tiles) ...[t, const SizedBox(height: 12)],
              ],
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    return Scaffold(
      appBar: AppBar(title: Text(tr('Emergency'))),
      body: SafeArea(
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Semantics(
                button: true,
                label: tr('I Need Medical Help'),
                child: SizedBox(
                  width: double.infinity,
                  height: 120,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    child: FilledButton(
                      style: FilledButton.styleFrom(
                        backgroundColor: Colors.red,
                        textStyle: const TextStyle(fontSize: 24),
                      ),
                      onPressed: controller.phase == AlertPhase.sending ||
                              controller.phase == AlertPhase.retrying
                          ? null
                          : controller.trigger,
                      child: Text(tr('I Need Medical Help')),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 24),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Text(
                  _statusText(controller.phase),
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 18),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _FallbackButton extends StatelessWidget {
  const _FallbackButton({required this.label, required this.onPressed});

  final String label;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 64,
      child: FilledButton(
        style: FilledButton.styleFrom(
          backgroundColor: Colors.red,
          textStyle: const TextStyle(fontSize: 20),
        ),
        onPressed: onPressed,
        child: Text(label),
      ),
    );
  }
}

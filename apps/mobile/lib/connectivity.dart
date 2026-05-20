/// `ConnectivityWatcher` seam for the Slice 11 offline indicator.
///
/// Same shape as `LocalReminderScheduler` in `medicine.dart` — the
/// production wiring is a Phase-2 task (Flutter platform channels via
/// `connectivity_plus` or equivalent). Adding the real dependency before
/// platform-native channels are configured would fail `flutter test` on
/// CI; the in-memory stub keeps the contract testable today.
///
/// The seam exposes an observable boolean shaped as a `ValueListenable`
/// — synchronous notifications, no stream-microtask delays in widget
/// tests. The Slice 6 offline-emergency queue (`EmergencyController` +
/// `FilePendingAlertStore`) remains the real source of truth for
/// retry/queue behavior; this watcher is the resident-visible cue only.

library;

import 'package:flutter/foundation.dart';

abstract class ConnectivityWatcher {
  /// True when the device most recently observed itself online. Defaults
  /// to optimistic-online so the offline banner is hidden by default and
  /// only appears once the watcher confirms a disconnection.
  bool get isOnline;

  /// Listenable view of the same value — wire a `ValueListenableBuilder`
  /// to react to transitions without manual subscription bookkeeping.
  ValueListenable<bool> get listenable;

  /// Stop watching and release any platform channel resources. No-op for
  /// the in-memory stub.
  Future<void> dispose();
}

/// Test/dev stub. Defaults to online; tests flip state via [setOnline] and
/// listeners fire synchronously so widget tests can pump once and observe
/// the new banner state.
class InMemoryConnectivityWatcher implements ConnectivityWatcher {
  InMemoryConnectivityWatcher({bool online = true})
      : _notifier = ValueNotifier<bool>(online);

  final ValueNotifier<bool> _notifier;

  @override
  bool get isOnline => _notifier.value;

  @override
  ValueListenable<bool> get listenable => _notifier;

  /// Flip state and notify listeners. The Slice 11 widget tests use this
  /// to assert the banner appears on offline and disappears on reconnect.
  void setOnline(bool online) {
    if (_notifier.value == online) return;
    _notifier.value = online;
  }

  @override
  Future<void> dispose() async {
    _notifier.dispose();
  }
}

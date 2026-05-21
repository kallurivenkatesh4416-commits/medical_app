/// `ConnectivityWatcher` seam for the Slice 11 offline indicator.
///
/// Slice 14 lands the real `connectivity_plus`-backed implementation behind
/// the same seam, so `flutter run` on a real Android device reflects actual
/// network state while `flutter test` continues to use [InMemoryConnectivityWatcher]
/// (no platform channel mocks needed).
///
/// The seam exposes an observable boolean shaped as a `ValueListenable`
/// — synchronous notifications, no stream-microtask delays in widget
/// tests. The Slice 6 offline-emergency queue (`EmergencyController` +
/// `FilePendingAlertStore`) remains the real source of truth for
/// retry/queue behavior; this watcher is the resident-visible cue only.

library;

import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
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

/// Production wiring — drives the same `ValueListenable<bool>` contract from
/// the device's actual `connectivity_plus` stream. The watcher starts
/// optimistic-online (matching the stub) so the offline banner only appears
/// once the platform confirms a disconnection; flipping ahead of the first
/// callback would briefly paint "offline" on every cold start.
///
/// **Not** used in widget tests. `flutter test` wires [InMemoryConnectivityWatcher]
/// via constructor injection from `main.dart`'s `connectivity` field so this
/// class is never instantiated in the test binding.
class ConnectivityPlusWatcher implements ConnectivityWatcher {
  ConnectivityPlusWatcher({Connectivity? connectivity})
      : _connectivity = connectivity ?? Connectivity(),
        _notifier = ValueNotifier<bool>(true) {
    _subscription = _connectivity.onConnectivityChanged
        .listen(_apply, onError: (_) => _notifier.value = false);
    // Seed with the current state — `onConnectivityChanged` fires on
    // transitions only, so without this the banner ignores a device that
    // booted offline.
    unawaited(_connectivity.checkConnectivity().then(_apply));
  }

  final Connectivity _connectivity;
  final ValueNotifier<bool> _notifier;
  StreamSubscription<List<ConnectivityResult>>? _subscription;

  void _apply(List<ConnectivityResult> results) {
    final online = results.any((r) => r != ConnectivityResult.none);
    if (_notifier.value != online) _notifier.value = online;
  }

  @override
  bool get isOnline => _notifier.value;

  @override
  ValueListenable<bool> get listenable => _notifier;

  @override
  Future<void> dispose() async {
    await _subscription?.cancel();
    _subscription = null;
    _notifier.dispose();
  }
}

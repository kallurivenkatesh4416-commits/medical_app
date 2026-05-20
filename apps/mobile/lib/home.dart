/// Slice 11 home / vitals / records / settings shells.
///
/// All four screens share two cross-cutting affordances:
///
/// 1. A **sticky `DISCLAIMER_SHORT` footer** — never scrolls away — on
///    every health-insight surface (home / vitals / records). The footer
///    is part of the Scaffold's `bottomNavigationBar` slot so it stays
///    pinned across list scrolls. Settings does NOT carry the footer (it
///    is not a health-insight screen — brief §2.1 / `docs/ux-copy.md`).
/// 2. A **`ConnectivityWatcher`-driven offline banner** between the
///    AppBar and the body. The banner only renders when the watcher
///    reports offline; the Slice 6 emergency offline queue handles the
///    real retry — this surface is the resident-visible cue.
///
/// Every list surface has an empty-state copy block from `safety.dart`
/// so a fresh resident sees a quiet helpful sentence rather than a blank
/// `ListView`.
///
/// The actual data (medicines / records / vitals) is **not** wired in
/// this slice — the screens render fixtures or the empty state from
/// constructor input. Real data loaders are downstream; the polish
/// surface is what Slice 11 ships.

library;

import 'package:flutter/material.dart';

import 'connectivity.dart';
import 'main.dart' show tr;
import 'safety.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({
    super.key,
    required this.connectivity,
    this.records = const <String>[],
    this.vitals = const <String>[],
    this.medicines = const <String>[],
  });

  final ConnectivityWatcher connectivity;
  final List<String> records;
  final List<String> vitals;
  final List<String> medicines;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final pages = <Widget>[
      HomeTab(medicines: widget.medicines, connectivity: widget.connectivity),
      VitalsTab(vitals: widget.vitals, connectivity: widget.connectivity),
      RecordsTab(records: widget.records, connectivity: widget.connectivity),
      const SettingsTab(),
    ];
    return Scaffold(
      appBar: AppBar(title: Text(tr(_titleFor(_tab)))),
      body: pages[_tab],
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _tab,
        onTap: (i) => setState(() => _tab = i),
        items: [
          BottomNavigationBarItem(icon: const Icon(Icons.home), label: tr('Home')),
          BottomNavigationBarItem(
            icon: const Icon(Icons.favorite_outline),
            label: tr('Vitals'),
          ),
          BottomNavigationBarItem(
            icon: const Icon(Icons.folder_outlined),
            label: tr('Records'),
          ),
          BottomNavigationBarItem(
            icon: const Icon(Icons.settings_outlined),
            label: tr('Settings'),
          ),
        ],
      ),
    );
  }

  String _titleFor(int tab) => const ['Home', 'Vitals', 'Records', 'Settings'][tab];
}

/// Shared scaffold for the three health-insight screens: offline banner
/// at the top, list body in the middle, sticky `DISCLAIMER_SHORT` at the
/// bottom. Settings reuses the offline banner via its own [Scaffold] but
/// does NOT carry the footer.
class _HealthScreenScaffold extends StatelessWidget {
  const _HealthScreenScaffold({
    required this.connectivity,
    required this.body,
  });

  final ConnectivityWatcher connectivity;
  final Widget body;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OfflineBanner(connectivity: connectivity),
        Expanded(child: body),
        const StickyDisclaimerShort(),
      ],
    );
  }
}

class OfflineBanner extends StatelessWidget {
  const OfflineBanner({super.key, required this.connectivity});

  final ConnectivityWatcher connectivity;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<bool>(
      valueListenable: connectivity.listenable,
      builder: (context, online, _) {
        if (online) return const SizedBox.shrink();
        return Semantics(
          liveRegion: true,
          label: tr(offlineBanner),
          child: Container(
            width: double.infinity,
            color: const Color(0xFFFFE6CC),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            child: Row(
              children: [
                const Icon(Icons.wifi_off, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    tr(offlineBanner),
                    style: const TextStyle(fontSize: 14),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// `DISCLAIMER_SHORT` pinned to the bottom of every health-insight
/// screen. Compact, never-truncated, two-line max.
class StickyDisclaimerShort extends StatelessWidget {
  const StickyDisclaimerShort({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: const Color(0xFFEFF3F8),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Semantics(
        label: tr(disclaimerShort),
        child: Text(
          tr(disclaimerShort),
          style: const TextStyle(fontSize: 13, color: Color(0xFF34404F)),
          textAlign: TextAlign.center,
        ),
      ),
    );
  }
}

class HomeTab extends StatelessWidget {
  const HomeTab({super.key, required this.medicines, required this.connectivity});

  final List<String> medicines;
  final ConnectivityWatcher connectivity;

  @override
  Widget build(BuildContext context) {
    return _HealthScreenScaffold(
      connectivity: connectivity,
      body: _EmptyOrList(
        items: medicines,
        empty: emptyStateMedicines,
        builder: (item) => ListTile(
          leading: const Icon(Icons.medication_outlined),
          title: Text(item, style: const TextStyle(fontSize: 18)),
        ),
      ),
    );
  }
}

class VitalsTab extends StatelessWidget {
  const VitalsTab({super.key, required this.vitals, required this.connectivity});

  final List<String> vitals;
  final ConnectivityWatcher connectivity;

  @override
  Widget build(BuildContext context) {
    return _HealthScreenScaffold(
      connectivity: connectivity,
      body: _EmptyOrList(
        items: vitals,
        empty: emptyStateVitals,
        builder: (item) => ListTile(
          leading: const Icon(Icons.monitor_heart_outlined),
          title: Text(item, style: const TextStyle(fontSize: 18)),
        ),
      ),
    );
  }
}

class RecordsTab extends StatelessWidget {
  const RecordsTab({super.key, required this.records, required this.connectivity});

  final List<String> records;
  final ConnectivityWatcher connectivity;

  @override
  Widget build(BuildContext context) {
    return _HealthScreenScaffold(
      connectivity: connectivity,
      body: _EmptyOrList(
        items: records,
        empty: emptyStateRecords,
        builder: (item) => ListTile(
          leading: const Icon(Icons.description_outlined),
          title: Text(item, style: const TextStyle(fontSize: 18)),
        ),
      ),
    );
  }
}

class SettingsTab extends StatelessWidget {
  const SettingsTab({super.key});

  @override
  Widget build(BuildContext context) {
    return ListView(
      children: [
        ListTile(
          leading: const Icon(Icons.devices_other_outlined),
          title: Text(
            tr('Connected devices'),
            style: const TextStyle(fontSize: 18),
          ),
          subtitle: Text(
            tr(connectedDevicesPhase2),
            style: const TextStyle(fontSize: 14),
          ),
        ),
        const Divider(),
      ],
    );
  }
}

class _EmptyOrList extends StatelessWidget {
  const _EmptyOrList({
    required this.items,
    required this.empty,
    required this.builder,
  });

  final List<String> items;
  final String empty;
  final Widget Function(String) builder;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 24),
          child: Semantics(
            label: tr(empty),
            child: Text(
              tr(empty),
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 16, color: Color(0xFF596273)),
            ),
          ),
        ),
      );
    }
    return ListView.builder(
      itemCount: items.length,
      itemBuilder: (_, i) => builder(items[i]),
    );
  }
}

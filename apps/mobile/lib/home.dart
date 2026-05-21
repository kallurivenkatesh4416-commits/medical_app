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
/// Slice 14 lands real data behind these tabs. When the production wiring
/// provides repositories, [HomeShell] renders the `Live*` variants which
/// fetch from the backend; widget tests omit the repos and the legacy
/// const-list shells keep rendering (so the Slice 11 polish suite stays
/// green with no HTTP mocks). The Slice 11 promise (sticky footer, empty
/// state, offline indicator, semantic labels) holds in both modes.

library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'auth/auth_repository.dart';
import 'connectivity.dart';
import 'main.dart' show UriLauncher, defaultLauncher, tr;
import 'medicine.dart';
import 'profile_repository.dart';
import 'records_repository.dart';
import 'safety.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({
    super.key,
    required this.connectivity,
    this.records = const <String>[],
    this.vitals = const <String>[],
    this.medicines = const <String>[],
    this.profileRepository,
    this.recordsRepository,
    this.medicineController,
    this.authRepository,
    this.recordLinkLauncher,
  });

  final ConnectivityWatcher connectivity;

  /// Legacy const lists — used by widget tests that exercise the shell
  /// without any HTTP wiring. Ignored when the corresponding repository
  /// is supplied.
  final List<String> records;
  final List<String> vitals;
  final List<String> medicines;

  /// Slice 14 production wiring. When all three are non-null the Live*
  /// tab variants render real data; when null the legacy const-list
  /// shells stay in place (the Slice 11 widget tests do this).
  final ProfileRepository? profileRepository;
  final RecordsRepository? recordsRepository;
  final MedicineController? medicineController;

  /// Slice 14 — used by Settings → Sign out. Null in widget tests.
  final AuthRepository? authRepository;

  /// Slice 14 — opens a signed record link in the platform browser/viewer.
  /// Tests inject a fake; production defaults to `url_launcher`.
  final UriLauncher? recordLinkLauncher;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final pages = <Widget>[
      _resolveHomeTab(),
      _resolveVitalsTab(),
      _resolveRecordsTab(),
      _resolveSettingsTab(),
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

  Widget _resolveHomeTab() {
    final controller = widget.medicineController;
    if (controller != null) {
      return LiveHomeTab(
        controller: controller,
        connectivity: widget.connectivity,
      );
    }
    return HomeTab(
      medicines: widget.medicines,
      connectivity: widget.connectivity,
    );
  }

  Widget _resolveVitalsTab() {
    return VitalsTab(vitals: widget.vitals, connectivity: widget.connectivity);
  }

  Widget _resolveRecordsTab() {
    final repo = widget.recordsRepository;
    if (repo != null) {
      return LiveRecordsTab(
        repository: repo,
        connectivity: widget.connectivity,
        launcher: widget.recordLinkLauncher ?? defaultLauncher,
      );
    }
    return RecordsTab(
      records: widget.records,
      connectivity: widget.connectivity,
    );
  }

  Widget _resolveSettingsTab() {
    final profile = widget.profileRepository;
    if (profile != null) {
      return LiveSettingsTab(
        profile: profile,
        auth: widget.authRepository,
      );
    }
    return const SettingsTab();
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
    this.floatingActionButton,
  });

  final ConnectivityWatcher connectivity;
  final Widget body;
  final Widget? floatingActionButton;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      floatingActionButton: floatingActionButton,
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          OfflineBanner(connectivity: connectivity),
          Expanded(child: body),
          const StickyDisclaimerShort(),
        ],
      ),
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

// --------------------------------------------------------------------------- //
// Legacy const-list tabs (Slice 11) — used by widget tests w/o repos          //
// --------------------------------------------------------------------------- //

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

// --------------------------------------------------------------------------- //
// Slice 14 — Live* tabs wired to real repositories                            //
// --------------------------------------------------------------------------- //

class LiveHomeTab extends StatefulWidget {
  const LiveHomeTab({
    super.key,
    required this.controller,
    required this.connectivity,
  });

  final MedicineController controller;
  final ConnectivityWatcher connectivity;

  @override
  State<LiveHomeTab> createState() => _LiveHomeTabState();
}

class _LiveHomeTabState extends State<LiveHomeTab> {
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      await widget.controller.refresh();
    } catch (_) {
      if (mounted) setState(() => _error = tr('Could not load medicines.'));
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _markTaken(MedicineSchedule s) async {
    final now = DateTime.now();
    await widget.controller.markTaken(s.id, now);
    await _refresh();
  }

  Future<void> _markSkipped(MedicineSchedule s) async {
    final now = DateTime.now();
    await widget.controller.markSkipped(s.id, now);
    await _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final schedules = widget.controller.schedules;
    return _HealthScreenScaffold(
      connectivity: widget.connectivity,
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ListView(
                    children: [
                      const SizedBox(height: 80),
                      Center(
                        child: Text(
                          _error!,
                          style: const TextStyle(fontSize: 16),
                        ),
                      ),
                    ],
                  )
                : schedules.isEmpty
                    ? ListView(
                        children: [
                          const SizedBox(height: 80),
                          Center(
                            child: Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 32),
                              child: Text(
                                tr(emptyStateMedicines),
                                style: const TextStyle(
                                  fontSize: 16,
                                  color: Color(0xFF596273),
                                ),
                                textAlign: TextAlign.center,
                              ),
                            ),
                          ),
                        ],
                      )
                    : ListView.builder(
                        itemCount: schedules.length,
                        itemBuilder: (_, i) {
                          final s = schedules[i];
                          final subtitle = [
                            if (s.dose != null && s.dose!.isNotEmpty) s.dose!,
                            s.frequency.replaceAll('_', ' '),
                            s.timesOfDay.join(', '),
                          ].where((p) => p.isNotEmpty).join(' · ');
                          return Card(
                            margin: const EdgeInsets.symmetric(
                                horizontal: 16, vertical: 6),
                            child: Padding(
                              padding: const EdgeInsets.all(12),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.stretch,
                                children: [
                                  ListTile(
                                    contentPadding: EdgeInsets.zero,
                                    leading: const Icon(Icons.medication_outlined),
                                    title: Text(
                                      s.name,
                                      style: const TextStyle(fontSize: 18),
                                    ),
                                    subtitle: subtitle.isEmpty
                                        ? null
                                        : Text(subtitle),
                                  ),
                                  Row(
                                    children: [
                                      Expanded(
                                        child: OutlinedButton(
                                          onPressed: () => _markSkipped(s),
                                          child: Text(tr('Skip')),
                                        ),
                                      ),
                                      const SizedBox(width: 8),
                                      Expanded(
                                        child: FilledButton(
                                          onPressed: () => _markTaken(s),
                                          child: Text(tr('I took it')),
                                        ),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
      ),
    );
  }
}

class LiveRecordsTab extends StatefulWidget {
  const LiveRecordsTab({
    super.key,
    required this.repository,
    required this.connectivity,
    required this.launcher,
  });

  final RecordsRepository repository;
  final ConnectivityWatcher connectivity;
  final UriLauncher launcher;

  @override
  State<LiveRecordsTab> createState() => LiveRecordsTabState();
}

class LiveRecordsTabState extends State<LiveRecordsTab> {
  bool _loading = true;
  String? _error;
  List<MedicalRecord> _records = const [];

  /// Test seam: an explicit upload trigger so widget tests can drive
  /// the upload path without firing a real `file_picker` (the picker
  /// plugin opens a platform sheet that flutter_test can't drive).
  Future<void> Function()? testUploadHook;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final records = await widget.repository.list();
      if (!mounted) return;
      setState(() {
        _records = records;
        _loading = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _error = tr('Could not load records.');
          _loading = false;
        });
      }
    }
  }

  Future<void> _openRecord(MedicalRecord record) async {
    final messenger = ScaffoldMessenger.of(context);
    final url = await widget.repository.signedLinkFor(record.id);
    if (!mounted) return;
    if (url == null) {
      messenger.showSnackBar(
        SnackBar(content: Text(tr('Could not generate a signed link.'))),
      );
      return;
    }
    final uri = Uri.parse(url);
    final ok = await widget.launcher(uri);
    if (!mounted) return;
    if (!ok) {
      messenger.showSnackBar(
        SnackBar(content: Text(tr('Could not open the record viewer.'))),
      );
    }
  }

  Future<void> _onUploadTap() async {
    final hook = testUploadHook;
    if (hook != null) {
      await hook();
      await _refresh();
      return;
    }
    // Production wiring lives in records_upload_picker.dart. The picker
    // is dynamically imported to keep file_picker out of the widget test
    // analyzer pass when the test never taps the FAB.
    await _runPickerUpload();
    await _refresh();
  }

  Future<void> _runPickerUpload() async {
    final picker = await _resolvePickerUpload(context);
    if (picker == null) return;
    final result = await picker(widget.repository);
    if (!mounted || result == null) return;
    final messenger = ScaffoldMessenger.of(context);
    if (result.ok) {
      messenger.showSnackBar(
        SnackBar(content: Text(tr('Record uploaded.'))),
      );
    } else {
      messenger.showSnackBar(
        SnackBar(
          content: Text(tr('Upload failed. Please try again.')),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return _HealthScreenScaffold(
      connectivity: widget.connectivity,
      floatingActionButton: Semantics(
        button: true,
        label: tr('Upload a record'),
        child: FloatingActionButton.extended(
          onPressed: _onUploadTap,
          icon: const Icon(Icons.upload_file),
          label: Text(tr('Upload')),
        ),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ListView(
                    children: [
                      const SizedBox(height: 80),
                      Center(
                        child: Text(_error!,
                            style: const TextStyle(fontSize: 16)),
                      ),
                    ],
                  )
                : _records.isEmpty
                    ? ListView(
                        children: [
                          const SizedBox(height: 80),
                          Center(
                            child: Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 32),
                              child: Text(
                                tr(emptyStateRecords),
                                style: const TextStyle(
                                  fontSize: 16,
                                  color: Color(0xFF596273),
                                ),
                                textAlign: TextAlign.center,
                              ),
                            ),
                          ),
                        ],
                      )
                    : ListView.builder(
                        itemCount: _records.length,
                        itemBuilder: (_, i) {
                          final r = _records[i];
                          final subtitle = [
                            r.recordType,
                            if (r.recordDate != null) r.recordDate,
                            if (r.source != null && r.source!.isNotEmpty)
                              r.source,
                          ].whereType<String>().join(' · ');
                          return ListTile(
                            leading: const Icon(Icons.description_outlined),
                            title: Text(r.fileName,
                                style: const TextStyle(fontSize: 18)),
                            subtitle: subtitle.isEmpty
                                ? null
                                : Text(subtitle),
                            trailing: const Icon(Icons.open_in_new),
                            onTap: () => _openRecord(r),
                          );
                        },
                      ),
      ),
    );
  }
}

/// Lazily resolves the production `file_picker` upload helper. Kept as a
/// runtime indirection so widget tests that never tap the FAB never load
/// the picker plugin (which would require a platform binding mock).
Future<Future<RecordUploadResult?> Function(RecordsRepository)?>
    _resolvePickerUpload(BuildContext context) async {
  // Production builds register a picker hook by mutating
  // [recordsPickerUploader]. When the hook is null (tests), the upload
  // surface is a no-op — the test seam [LiveRecordsTabState.testUploadHook]
  // provides the alternate path.
  return recordsPickerUploader;
}

/// Top-level seam mutated by `records_upload_picker.dart` at app startup
/// (production-only). Null in widget tests, so the FAB no-ops without
/// touching the picker plugin.
Future<RecordUploadResult?> Function(RecordsRepository)?
    recordsPickerUploader;

class LiveSettingsTab extends StatefulWidget {
  const LiveSettingsTab({super.key, required this.profile, this.auth});

  final ProfileRepository profile;
  final AuthRepository? auth;

  @override
  State<LiveSettingsTab> createState() => _LiveSettingsTabState();
}

class _LiveSettingsTabState extends State<LiveSettingsTab> {
  List<ResidentConsent> _consents = const [];
  ResidentProfile? _profile;
  bool _loading = true;
  String? _busyConsent;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    final results = await Future.wait([
      widget.profile.readProfile(),
      widget.profile.readConsents(),
    ]);
    if (!mounted) return;
    setState(() {
      _profile = results[0] as ResidentProfile?;
      _consents = results[1] as List<ResidentConsent>;
      _loading = false;
    });
  }

  Future<void> _toggle(ResidentConsent c, bool next) async {
    setState(() => _busyConsent = c.type);
    final ok = await widget.profile.updateConsent(
      consentType: c.type,
      granted: next,
    );
    if (!mounted) return;
    if (ok) {
      // Re-read so a `data_storage` revoke (which closes the account) and
      // any downstream cascade is reflected immediately.
      await _refresh();
    }
    setState(() => _busyConsent = null);
  }

  Future<void> _signOut() async {
    final auth = widget.auth;
    if (auth == null) return;
    await auth.logout();
    if (!mounted) return;
    Navigator.of(context).popUntil((route) => route.isFirst);
  }

  String _consentLabelFor(String type) {
    switch (type) {
      case 'data_storage':
        return 'Store my data (required)';
      case 'emergency_share_with_doctor':
        return 'Share my profile with the on-duty doctor';
      case 'emergency_share_with_hospital':
        return 'Share my handover summary with hospitals';
      case 'family_member_access':
        return 'Let family members see my profile';
      case 'medicine_reminder_notifications':
        return 'Send me medicine reminders';
      default:
        return type;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    final p = _profile;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        children: [
          if (p != null)
            ListTile(
              leading: const Icon(Icons.person_outline),
              title: Text(p.fullName,
                  style: const TextStyle(fontSize: 18)),
              subtitle: Text(
                [
                  if (p.flatVillaNumber.isNotEmpty) 'Flat ${p.flatVillaNumber}',
                  if (p.bloodGroup != null && p.bloodGroup!.isNotEmpty)
                    'Blood ${p.bloodGroup!}',
                  if (p.primaryContactName != null)
                    'Contact: ${p.primaryContactName}',
                ].join(' · '),
              ),
            ),
          const Divider(),
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
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: Text(
              tr('Consents'),
              style: const TextStyle(fontSize: 14, color: Color(0xFF596273)),
            ),
          ),
          for (final c in _consents)
            SwitchListTile(
              title: Text(tr(_consentLabelFor(c.type)),
                  style: const TextStyle(fontSize: 16)),
              value: c.granted,
              onChanged: _busyConsent != null || c.type == 'data_storage'
                  ? null
                  : (next) => _toggle(c, next),
            ),
          const Divider(),
          if (widget.auth != null)
            ListTile(
              leading: const Icon(Icons.logout),
              title: Text(tr('Sign out'),
                  style: const TextStyle(fontSize: 18)),
              onTap: _signOut,
            ),
        ],
      ),
    );
  }
}

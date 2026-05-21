/// Slice 11 + 14 onboarding flow.
///
/// Order matches `docs/onboarding-flow.md`:
///
///   Disclaimer ack → Profile (name / DOB / gender / project / flat) →
///   Emergency contacts (1–3) → Medical history → Consents → Submit
///
/// The acknowledgment is **not** a consent row — it's an audit-only event
/// (`AuditAction.DISCLAIMER_ACKNOWLEDGED`). The backend onboarding service
/// already rejects `disclaimer_acknowledged=False` and writes the audit
/// row inside the same atomic transaction as the user / profile / contacts
/// / consents inserts ([onboarding_service.py:67]). Slice 11 surfaces this
/// as a dedicated screen; Slice 14 stitches the rest of the wizard onto it
/// and submits one payload to `POST /api/v1/onboarding/complete`.
///
/// The screen carries the **literal `DISCLAIMER_FULL`** text from
/// `safety.dart` — the wording history in `docs/ux-copy.md` records that
/// paraphrasing reintroduced "diagnose" once and was reverted.

library;

import 'package:flutter/material.dart';

import 'connectivity.dart';
import 'home.dart';
import 'main.dart' show tr;
import 'onboarding_repository.dart';
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

// --------------------------------------------------------------------------- //
// Slice 14 — multi-step onboarding wizard                                     //
// --------------------------------------------------------------------------- //

/// Steps follow `docs/onboarding-flow.md`. Disclaimer is step 0 so the
/// resident cannot reach any data-entry screen without acknowledging.
enum _OnboardingStep {
  disclaimer,
  profile,
  contacts,
  medical,
  consents,
}

class _OnboardingDraft {
  String fullName = '';
  DateTime? dob;
  String gender = 'prefer_not_to_say';
  String? projectId;
  String flatVillaNumber = '';
  List<OnboardingContact> contacts = [];
  String? bloodGroup;
  List<String> diseases = [];
  List<String> allergies = [];
  List<String> surgeries = [];
  String? preferredHospital;
  Map<OnboardingConsent, bool> consents = {
    for (final c in OnboardingConsent.values) c: c.required,
  };

  bool get profileComplete =>
      fullName.trim().isNotEmpty &&
      dob != null &&
      projectId != null &&
      flatVillaNumber.trim().isNotEmpty;

  bool get contactsComplete => contacts.isNotEmpty && contacts.length <= 3;

  bool get consentsComplete =>
      consents[OnboardingConsent.dataStorage] == true;
}

class OnboardingFlow extends StatefulWidget {
  const OnboardingFlow({
    super.key,
    required this.repository,
    required this.connectivity,
    this.onComplete,
  });

  final OnboardingRepository repository;
  final ConnectivityWatcher connectivity;

  /// Called after the backend accepts the payload and the token pair has
  /// been persisted. Default routes to the [HomeShell]; tests may supply
  /// a no-op so the assertion looks at the persisted state.
  final void Function(BuildContext context)? onComplete;

  @override
  State<OnboardingFlow> createState() => _OnboardingFlowState();
}

class _OnboardingFlowState extends State<OnboardingFlow> {
  _OnboardingStep _step = _OnboardingStep.disclaimer;
  final _OnboardingDraft _draft = _OnboardingDraft();
  List<OnboardingProject> _projects = const [];
  bool _loadingProjects = false;
  bool _submitting = false;
  String? _submitError;

  @override
  void initState() {
    super.initState();
    _loadProjects();
  }

  Future<void> _loadProjects() async {
    setState(() => _loadingProjects = true);
    final projects = await widget.repository.listProjects();
    if (!mounted) return;
    setState(() {
      _projects = projects;
      _loadingProjects = false;
      // Pre-select the only project so the demo demo flow is one tap less
      // when a single tenant is configured.
      if (projects.length == 1) _draft.projectId = projects.first.id;
    });
  }

  void _advance() {
    final next = _OnboardingStep.values[_step.index + 1];
    setState(() => _step = next);
  }

  void _back() {
    if (_step == _OnboardingStep.disclaimer) {
      Navigator.of(context).maybePop();
      return;
    }
    final prev = _OnboardingStep.values[_step.index - 1];
    setState(() => _step = prev);
  }

  Future<void> _submit() async {
    if (!_draft.consentsComplete) {
      setState(
        () => _submitError = tr(
          'Data storage consent is required to use the app.',
        ),
      );
      return;
    }
    final payload = OnboardingPayload(
      fullName: _draft.fullName.trim(),
      dob: _draft.dob!,
      gender: _draft.gender,
      projectId: _draft.projectId!,
      flatVillaNumber: _draft.flatVillaNumber.trim(),
      contacts: _draft.contacts,
      consents: _draft.consents,
      bloodGroup: _draft.bloodGroup,
      diseases: _draft.diseases,
      allergies: _draft.allergies,
      surgeries: _draft.surgeries,
      preferredHospital: _draft.preferredHospital,
    );
    setState(() {
      _submitting = true;
      _submitError = null;
    });
    final result = await widget.repository.complete(payload);
    if (!mounted) return;
    setState(() => _submitting = false);
    if (!result.ok) {
      setState(
        () => _submitError = _errorCopyFor(result.errorCode),
      );
      return;
    }
    final cb = widget.onComplete;
    if (cb != null) {
      cb(context);
      return;
    }
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => HomeShell(connectivity: widget.connectivity),
      ),
    );
  }

  String _errorCopyFor(String? code) {
    switch (code) {
      case 'already_registered':
        return tr('This phone is already registered. Please sign in instead.');
      case 'registration_expired':
        return tr('Your verification expired. Please sign in again.');
      case 'idempotency_key_conflict':
        return tr('A prior registration is in progress. Please sign in instead.');
      default:
        return tr('Could not complete onboarding. Please check your details.');
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_submitting) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    final title = _titleFor(_step);
    return Scaffold(
      appBar: AppBar(title: Text(tr(title))),
      body: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _StepIndicator(step: _step.index + 1, total: _OnboardingStep.values.length),
            Expanded(child: _stepBody()),
            if (_submitError != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Text(
                  _submitError!,
                  style: const TextStyle(color: Colors.red, fontSize: 14),
                ),
              ),
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 12, 24, 18),
              child: Row(
                children: [
                  Expanded(
                    child: SizedBox(
                      height: 56,
                      child: OutlinedButton(
                        onPressed: _back,
                        child: Text(tr('Back')),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: SizedBox(
                      height: 56,
                      child: FilledButton(
                        onPressed: _canAdvance() ? _onPrimaryTap : null,
                        child: Text(tr(_primaryLabelFor(_step))),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _onPrimaryTap() {
    if (_step == _OnboardingStep.consents) {
      _submit();
      return;
    }
    _advance();
  }

  bool _canAdvance() {
    switch (_step) {
      case _OnboardingStep.disclaimer:
        return true; // The disclaimer screen owns its own gate.
      case _OnboardingStep.profile:
        return _draft.profileComplete;
      case _OnboardingStep.contacts:
        return _draft.contactsComplete;
      case _OnboardingStep.medical:
        return true; // Every field is optional except blood group rules (none).
      case _OnboardingStep.consents:
        return _draft.consentsComplete;
    }
  }

  String _primaryLabelFor(_OnboardingStep step) {
    if (step == _OnboardingStep.consents) return 'Complete onboarding';
    return 'Continue';
  }

  String _titleFor(_OnboardingStep step) {
    switch (step) {
      case _OnboardingStep.disclaimer:
        return 'Important Safety Notice';
      case _OnboardingStep.profile:
        return 'Your details';
      case _OnboardingStep.contacts:
        return 'Emergency contacts';
      case _OnboardingStep.medical:
        return 'Medical history';
      case _OnboardingStep.consents:
        return 'Consents';
    }
  }

  Widget _stepBody() {
    switch (_step) {
      case _OnboardingStep.disclaimer:
        return _DisclaimerStep(onAcknowledged: _advance);
      case _OnboardingStep.profile:
        return _ProfileStep(
          draft: _draft,
          projects: _projects,
          loadingProjects: _loadingProjects,
          onChanged: () => setState(() {}),
        );
      case _OnboardingStep.contacts:
        return _ContactsStep(
          draft: _draft,
          onChanged: () => setState(() {}),
        );
      case _OnboardingStep.medical:
        return _MedicalStep(
          draft: _draft,
          onChanged: () => setState(() {}),
        );
      case _OnboardingStep.consents:
        return _ConsentsStep(
          draft: _draft,
          onChanged: () => setState(() {}),
        );
    }
  }
}

class _StepIndicator extends StatelessWidget {
  const _StepIndicator({required this.step, required this.total});
  final int step;
  final int total;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 0),
      child: Row(
        children: [
          for (var i = 1; i <= total; i++) ...[
            Expanded(
              child: Container(
                height: 4,
                decoration: BoxDecoration(
                  color: i <= step
                      ? const Color(0xFF1F6F5B)
                      : const Color(0xFFD7DDE6),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            if (i < total) const SizedBox(width: 4),
          ],
        ],
      ),
    );
  }
}

class _DisclaimerStep extends StatefulWidget {
  const _DisclaimerStep({required this.onAcknowledged});
  final VoidCallback onAcknowledged;

  @override
  State<_DisclaimerStep> createState() => _DisclaimerStepState();
}

class _DisclaimerStepState extends State<_DisclaimerStep> {
  bool _read = false;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
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
          const Divider(height: 24),
          Semantics(
            label: tr('Confirm I have read and understood the notice'),
            child: CheckboxListTile(
              value: _read,
              controlAffinity: ListTileControlAffinity.leading,
              contentPadding: EdgeInsets.zero,
              title: Text(
                tr('I have read and understood the above.'),
                style: const TextStyle(fontSize: 18),
              ),
              onChanged: (v) => setState(() => _read = v ?? false),
            ),
          ),
          const SizedBox(height: 8),
          Semantics(
            button: true,
            label: tr('I understand — continue'),
            child: SizedBox(
              height: 56,
              child: FilledButton(
                onPressed: _read ? widget.onAcknowledged : null,
                child: Text(tr('I understand — continue')),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ProfileStep extends StatelessWidget {
  const _ProfileStep({
    required this.draft,
    required this.projects,
    required this.loadingProjects,
    required this.onChanged,
  });

  final _OnboardingDraft draft;
  final List<OnboardingProject> projects;
  final bool loadingProjects;
  final VoidCallback onChanged;

  Future<void> _pickDob(BuildContext context) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: draft.dob ?? DateTime(now.year - 30),
      firstDate: DateTime(1900),
      lastDate: now,
    );
    if (picked != null) {
      draft.dob = picked;
      onChanged();
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _LabeledField(
            label: 'Full name',
            child: TextFormField(
              initialValue: draft.fullName,
              onChanged: (v) {
                draft.fullName = v;
                onChanged();
              },
              decoration: const InputDecoration(border: OutlineInputBorder()),
              style: const TextStyle(fontSize: 18),
            ),
          ),
          _LabeledField(
            label: 'Date of birth',
            child: OutlinedButton(
              onPressed: () => _pickDob(context),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  draft.dob == null
                      ? tr('Pick a date')
                      : formatYyyyMmDd(draft.dob!),
                  style: const TextStyle(fontSize: 18),
                ),
              ),
            ),
          ),
          _LabeledField(
            label: 'Gender',
            child: DropdownButtonFormField<String>(
              initialValue: draft.gender,
              decoration: const InputDecoration(border: OutlineInputBorder()),
              items: const [
                DropdownMenuItem(value: 'male', child: Text('Male')),
                DropdownMenuItem(value: 'female', child: Text('Female')),
                DropdownMenuItem(value: 'other', child: Text('Other')),
                DropdownMenuItem(
                  value: 'prefer_not_to_say',
                  child: Text('Prefer not to say'),
                ),
              ],
              onChanged: (v) {
                if (v != null) {
                  draft.gender = v;
                  onChanged();
                }
              },
            ),
          ),
          _LabeledField(
            label: 'Residence (project)',
            child: loadingProjects
                ? const LinearProgressIndicator()
                : DropdownButtonFormField<String>(
                    initialValue: draft.projectId,
                    decoration:
                        const InputDecoration(border: OutlineInputBorder()),
                    items: [
                      for (final p in projects)
                        DropdownMenuItem(value: p.id, child: Text(p.name)),
                    ],
                    onChanged: (v) {
                      draft.projectId = v;
                      onChanged();
                    },
                  ),
          ),
          _LabeledField(
            label: 'Flat / villa number',
            child: TextFormField(
              initialValue: draft.flatVillaNumber,
              onChanged: (v) {
                draft.flatVillaNumber = v;
                onChanged();
              },
              decoration: const InputDecoration(border: OutlineInputBorder()),
              style: const TextStyle(fontSize: 18),
            ),
          ),
        ],
      ),
    );
  }
}

class _ContactsStep extends StatefulWidget {
  const _ContactsStep({required this.draft, required this.onChanged});
  final _OnboardingDraft draft;
  final VoidCallback onChanged;

  @override
  State<_ContactsStep> createState() => _ContactsStepState();
}

class _ContactsStepState extends State<_ContactsStep> {
  void _add() {
    if (widget.draft.contacts.length >= 3) return;
    setState(() {
      widget.draft.contacts.add(
        OnboardingContact(
          name: '',
          phone: '',
          isPrimary: widget.draft.contacts.isEmpty,
        ),
      );
    });
    widget.onChanged();
  }

  void _remove(int index) {
    setState(() {
      widget.draft.contacts.removeAt(index);
      // Re-anchor a primary so there is always at least one when non-empty.
      if (widget.draft.contacts.isNotEmpty &&
          !widget.draft.contacts.any((c) => c.isPrimary)) {
        widget.draft.contacts[0] = OnboardingContact(
          name: widget.draft.contacts[0].name,
          phone: widget.draft.contacts[0].phone,
          relation: widget.draft.contacts[0].relation,
          isPrimary: true,
        );
      }
    });
    widget.onChanged();
  }

  void _setField(int i, OnboardingContact next) {
    widget.draft.contacts[i] = next;
    widget.onChanged();
  }

  @override
  void initState() {
    super.initState();
    if (widget.draft.contacts.isEmpty) {
      // Seed one empty primary contact so the resident sees the form.
      widget.draft.contacts.add(
        const OnboardingContact(name: '', phone: '', isPrimary: true),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
      itemCount: widget.draft.contacts.length + 1,
      itemBuilder: (_, i) {
        if (i == widget.draft.contacts.length) {
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: OutlinedButton.icon(
              onPressed: widget.draft.contacts.length >= 3 ? null : _add,
              icon: const Icon(Icons.add),
              label: Text(
                tr('Add another contact (up to 3)'),
                style: const TextStyle(fontSize: 16),
              ),
            ),
          );
        }
        final c = widget.draft.contacts[i];
        return Card(
          margin: const EdgeInsets.only(bottom: 12),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _LabeledField(
                  label: 'Contact name',
                  child: TextFormField(
                    initialValue: c.name,
                    onChanged: (v) => _setField(
                      i,
                      OnboardingContact(
                        name: v,
                        phone: c.phone,
                        relation: c.relation,
                        isPrimary: c.isPrimary,
                      ),
                    ),
                    decoration:
                        const InputDecoration(border: OutlineInputBorder()),
                    style: const TextStyle(fontSize: 18),
                  ),
                ),
                _LabeledField(
                  label: 'Contact phone',
                  child: TextFormField(
                    initialValue: c.phone,
                    keyboardType: TextInputType.phone,
                    onChanged: (v) => _setField(
                      i,
                      OnboardingContact(
                        name: c.name,
                        phone: v,
                        relation: c.relation,
                        isPrimary: c.isPrimary,
                      ),
                    ),
                    decoration:
                        const InputDecoration(border: OutlineInputBorder()),
                    style: const TextStyle(fontSize: 18),
                  ),
                ),
                _LabeledField(
                  label: 'Relation (optional)',
                  child: TextFormField(
                    initialValue: c.relation ?? '',
                    onChanged: (v) => _setField(
                      i,
                      OnboardingContact(
                        name: c.name,
                        phone: c.phone,
                        relation: v,
                        isPrimary: c.isPrimary,
                      ),
                    ),
                    decoration:
                        const InputDecoration(border: OutlineInputBorder()),
                    style: const TextStyle(fontSize: 18),
                  ),
                ),
                SwitchListTile(
                  value: c.isPrimary,
                  contentPadding: EdgeInsets.zero,
                  title: Text(
                    tr('Primary contact'),
                    style: const TextStyle(fontSize: 16),
                  ),
                  onChanged: (v) {
                    // Only one primary at a time.
                    setState(() {
                      for (var j = 0; j < widget.draft.contacts.length; j++) {
                        final other = widget.draft.contacts[j];
                        widget.draft.contacts[j] = OnboardingContact(
                          name: other.name,
                          phone: other.phone,
                          relation: other.relation,
                          isPrimary: j == i ? v : false,
                        );
                      }
                    });
                    widget.onChanged();
                  },
                ),
                if (widget.draft.contacts.length > 1)
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton.icon(
                      onPressed: () => _remove(i),
                      icon: const Icon(Icons.delete_outline),
                      label: Text(tr('Remove')),
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

class _MedicalStep extends StatelessWidget {
  const _MedicalStep({required this.draft, required this.onChanged});
  final _OnboardingDraft draft;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _LabeledField(
            label: 'Blood group',
            child: TextFormField(
              initialValue: draft.bloodGroup ?? '',
              onChanged: (v) {
                draft.bloodGroup = v;
                onChanged();
              },
              decoration: const InputDecoration(
                border: OutlineInputBorder(),
                hintText: 'e.g. O+, A-, B+',
              ),
              style: const TextStyle(fontSize: 18),
            ),
          ),
          _CsvList(
            label: 'Known diseases',
            hint: 'e.g. Hypertension, Diabetes',
            initial: draft.diseases,
            onChanged: (v) {
              draft.diseases = v;
              onChanged();
            },
          ),
          _CsvList(
            label: 'Allergies',
            hint: 'e.g. Penicillin, Peanuts',
            initial: draft.allergies,
            onChanged: (v) {
              draft.allergies = v;
              onChanged();
            },
          ),
          _CsvList(
            label: 'Surgeries',
            hint: 'e.g. Appendectomy 2018',
            initial: draft.surgeries,
            onChanged: (v) {
              draft.surgeries = v;
              onChanged();
            },
          ),
          _LabeledField(
            label: 'Preferred hospital (optional)',
            child: TextFormField(
              initialValue: draft.preferredHospital ?? '',
              onChanged: (v) {
                draft.preferredHospital = v;
                onChanged();
              },
              decoration: const InputDecoration(border: OutlineInputBorder()),
              style: const TextStyle(fontSize: 18),
            ),
          ),
        ],
      ),
    );
  }
}

class _ConsentsStep extends StatelessWidget {
  const _ConsentsStep({required this.draft, required this.onChanged});
  final _OnboardingDraft draft;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
      children: [
        Text(
          tr('Please review the consents below. You can change these later in Settings.'),
          style: const TextStyle(fontSize: 16),
        ),
        const SizedBox(height: 12),
        for (final consent in OnboardingConsent.values)
          Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: SwitchListTile(
              value: draft.consents[consent] ?? false,
              title: Text(
                tr(_consentTitleFor(consent)),
                style: const TextStyle(fontSize: 18),
              ),
              subtitle: Text(
                tr(_consentSummaryFor(consent)),
                style: const TextStyle(fontSize: 14),
              ),
              onChanged: consent.required
                  ? null // data_storage is required — toggle is locked on.
                  : (v) {
                      draft.consents[consent] = v;
                      onChanged();
                    },
            ),
          ),
      ],
    );
  }

  String _consentTitleFor(OnboardingConsent c) {
    switch (c) {
      case OnboardingConsent.dataStorage:
        return 'Store my data (required)';
      case OnboardingConsent.emergencyShareWithDoctor:
        return 'Share my profile with the on-duty doctor';
      case OnboardingConsent.emergencyShareWithHospital:
        return 'Share my handover summary with the receiving hospital';
      case OnboardingConsent.familyMemberAccess:
        return 'Let family members see my profile';
      case OnboardingConsent.medicineReminderNotifications:
        return 'Send me medicine reminders';
    }
  }

  String _consentSummaryFor(OnboardingConsent c) {
    switch (c) {
      case OnboardingConsent.dataStorage:
        return 'Needed to use the app. You can request deletion any time.';
      case OnboardingConsent.emergencyShareWithDoctor:
        return 'Used only when you tap the emergency button.';
      case OnboardingConsent.emergencyShareWithHospital:
        return 'A signed PDF link that expires after 15 minutes.';
      case OnboardingConsent.familyMemberAccess:
        return 'Family members must be added by you. Off by default.';
      case OnboardingConsent.medicineReminderNotifications:
        return 'Device-only reminders. No data leaves the phone.';
    }
  }
}

class _LabeledField extends StatelessWidget {
  const _LabeledField({required this.label, required this.child});
  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            tr(label),
            style: const TextStyle(fontSize: 14, color: Color(0xFF596273)),
          ),
          const SizedBox(height: 6),
          child,
        ],
      ),
    );
  }
}

class _CsvList extends StatelessWidget {
  const _CsvList({
    required this.label,
    required this.hint,
    required this.initial,
    required this.onChanged,
  });

  final String label;
  final String hint;
  final List<String> initial;
  final void Function(List<String>) onChanged;

  @override
  Widget build(BuildContext context) {
    return _LabeledField(
      label: label,
      child: TextFormField(
        initialValue: initial.join(', '),
        onChanged: (v) {
          final items = [
            for (final part in v.split(',')) part.trim(),
          ].where((s) => s.isNotEmpty).toList();
          onChanged(items);
        },
        decoration: InputDecoration(
          border: const OutlineInputBorder(),
          hintText: hint,
          helperText: tr('Separate items with commas.'),
        ),
        style: const TextStyle(fontSize: 18),
      ),
    );
  }
}

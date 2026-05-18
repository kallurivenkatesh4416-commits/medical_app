# Mobile — Resident App (Flutter)

```bash
flutter pub get
flutter analyze
flutter test
flutter run
```

Skeleton only (Slice 1): splash screen carries the offline **Call 108** action
(brief §2.2 / §14). OTP login, profile, records, the one-tap emergency button,
medicine reminders, and full elderly-UX polish land in later slices. All
user-facing strings go through `tr('...')` for Phase-2 i18n.

> Note: scaffolded manually (no `flutter create` in this environment). Run
> `flutter create .` to generate the native `android/` and `ios/` folders before
> building on a device.

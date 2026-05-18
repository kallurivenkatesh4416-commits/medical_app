import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/main.dart';

void main() {
  testWidgets('Splash always shows an offline Call 108 action', (tester) async {
    await tester.pumpWidget(MedEmergencyApp(launcher: (_) async => true));
    expect(find.widgetWithText(FilledButton, 'Call 108'), findsOneWidget);
  });

  testWidgets('Tapping Call 108 launches the tel:108 dialer', (tester) async {
    Uri? launched;
    await tester.pumpWidget(
      MedEmergencyApp(
        launcher: (uri) async {
          launched = uri;
          return true;
        },
      ),
    );

    await tester.tap(find.widgetWithText(FilledButton, 'Call 108'));
    await tester.pump();

    expect(launched, isNotNull);
    expect(launched!.scheme, 'tel');
    expect(launched!.path, '108');
  });

  testWidgets('Launcher failure shows a visible fallback message', (tester) async {
    await tester.pumpWidget(MedEmergencyApp(launcher: (_) async => false));

    await tester.tap(find.widgetWithText(FilledButton, 'Call 108'));
    await tester.pump(); // start the SnackBar animation

    expect(
      find.text('Could not open the dialer. Please dial 108 directly.'),
      findsOneWidget,
    );
  });

  testWidgets('Launcher throwing also shows the fallback message', (tester) async {
    await tester.pumpWidget(
      MedEmergencyApp(
        launcher: (_) async {
          throw Exception('no dialer');
        },
      ),
    );

    await tester.tap(find.widgetWithText(FilledButton, 'Call 108'));
    await tester.pump();

    expect(
      find.text('Could not open the dialer. Please dial 108 directly.'),
      findsOneWidget,
    );
  });
}

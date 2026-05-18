import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:med_emergency_mobile/main.dart';

void main() {
  testWidgets('Splash always shows an offline Call 108 action', (tester) async {
    await tester.pumpWidget(const MedEmergencyApp());
    expect(find.widgetWithText(FilledButton, 'Call 108'), findsOneWidget);
  });
}

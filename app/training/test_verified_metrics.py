from datetime import date
from decimal import Decimal
from django.test import TestCase
from .models import Employee, Training
from .analytics import statistics
from .dashboard import executive_data
from .indicators import analyse_indicators


class VerifiedMetricTests(TestCase):
    def setUp(self):
        self.person = Employee.objects.create(number='VERIFY', name='Pessoa de teste', direction='Teste')
        self.row = Training.objects.create(employee=self.person, title='Teste', start=date(2025,1,1), end=date(2025,1,2), days=2, hours_per_day=7, attendance=2, status='Concluído')

    def test_positive_unverified_values_are_preserved_but_not_measured(self):
        r = self.row
        self.assertEqual(r.attendance, 2)
        self.assertIsNone(r.completed_hours)
        self.assertIsNone(r.attendance_percent)
        self.assertIsNone(r.absences)
        stats = statistics([r])
        self.assertIsNone(stats['attendance'])
        self.assertEqual(stats['hours'], 0)
        self.assertEqual(stats['verified_hours_records'], 0)
        self.assertEqual(stats['hours_pending_count'], 1)
        dashboard = executive_data([r], [self.person])
        self.assertEqual(dashboard['kpis'][1]['value'], 0)
        self.assertIsNone(dashboard['kpis'][4]['value'])
        self.assertEqual(analyse_indicators([r])['attendance_count'], 0)

    def test_verified_zero_is_real_zero_not_unknown(self):
        self.row.attendance = 0
        self.row.attendance_confirmed = True
        self.assertEqual(self.row.completed_hours, 0)
        self.assertEqual(self.row.attendance_percent, 0)
        self.assertEqual(self.row.absences, 2)
        dashboard = executive_data([self.row], [self.person])
        self.assertEqual(dashboard['kpis'][4]['value'], 0)
        self.assertEqual(statistics([self.row])['attendance'], 0)

    def test_only_verified_denominator_and_same_metrics_across_modules(self):
        unknown = self.row
        verified = Training(employee=self.person, title='Confirmada', start=date(2025,1,1), end=date(2025,1,3), days=3, hours_per_day=7, attendance=1, attendance_confirmed=True, status='Concluído')
        stats = statistics([unknown,verified])
        dashboard = executive_data([unknown,verified], [self.person])
        indicators = analyse_indicators([unknown,verified])
        self.assertEqual(stats['attendance_days'], indicators['days'])
        self.assertEqual(stats['presences'], indicators['presences'])
        self.assertEqual(stats['hours'], dashboard['stats']['hours'])
        self.assertEqual(stats['hours'], Decimal('7'))
        self.assertEqual(stats['attendance'], Decimal('33.3'))

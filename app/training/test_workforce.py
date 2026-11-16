from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase
from .analytics import statistics
from .kpi_service import Scope
from .models import Employee, Training, WorkforceReference
from .workforce import coverage, find_reference

D = Decimal
JAN = (date(2026, 1, 1), date(2026, 1, 31))


def scope(filters=None, start=JAN[0], end=JAN[1], basis='session_date'):
    return Scope(filters or {}, 'Janeiro 2026', basis, 'current_registry', start, end)


def ref(headcount=200, start=JAN[0], end=JAN[1], direction='', category='', gender=''):
    return SimpleNamespace(period_start=start, period_end=end, direction=direction, category=category,
                           gender=gender, headcount=headcount)


class ReferenceMatchingTests(SimpleTestCase):
    def test_reference_for_same_period_and_scope_is_used(self):
        result = coverage(50, 100, scope(), [ref(200)])
        self.assertEqual((result['value'], result['denominator'], result['source']), (D('25.00'), 200, 'drh_reference'))

    def test_without_reference_the_current_registry_is_used_and_labelled(self):
        result = coverage(50, 100, scope(), [])
        self.assertEqual((result['value'], result['source']), (D('50.00'), 'current_registry'))
        self.assertEqual(result['label'], 'Cadastro actual disponível')

    def test_global_reference_does_not_serve_a_direction_filter(self):
        filtered = scope({'direction': 'DPO'})
        self.assertIsNone(find_reference(filtered, [ref(200)]))
        self.assertIsNotNone(find_reference(filtered, [ref(40, direction='DPO')]))

    def test_monthly_reference_does_not_serve_the_year(self):
        year = scope(start=date(2026, 1, 1), end=date(2026, 12, 31))
        self.assertIsNone(find_reference(year, [ref(200)]))

    def test_global_view_without_period_has_no_reference(self):
        self.assertIsNone(find_reference(scope(start=None, end=None), [ref(200)]))

    def test_over_one_hundred_percent_is_flagged_not_clamped(self):
        result = coverage(120, 100, scope(), [ref(100)])
        self.assertEqual((result['value'], result['status']), (D('120.00'), 'inconsistent'))

    def test_zero_denominator_gives_no_data(self):
        self.assertIsNone(coverage(0, 0, scope(), [])['value'])


class WorkforceReferenceModelTests(TestCase):
    def test_validation_and_unique_scope(self):
        bad = WorkforceReference(period_start=JAN[1], period_end=JAN[0], headcount=0, responsible='DRH')
        with self.assertRaises(ValidationError) as error:
            bad.full_clean()
        self.assertIn('period_end', error.exception.message_dict)
        self.assertIn('headcount', error.exception.message_dict)
        WorkforceReference.objects.create(period_start=JAN[0], period_end=JAN[1], headcount=10, responsible='DRH')
        with self.assertRaises(IntegrityError):
            WorkforceReference.objects.create(period_start=JAN[0], period_end=JAN[1], headcount=12, responsible='DRH')

    def test_statistics_use_the_drh_reference(self):
        person = Employee.objects.create(number='WF1', name='Synthetic')
        Employee.objects.create(number='WF2', name='Synthetic two')
        Training.objects.create(employee=person, title='Curso', start=JAN[0], end=JAN[0], days=1,
                                hours_per_day=7, attendance=1, attendance_confirmed=True, status='Concluído')
        WorkforceReference.objects.create(period_start=JAN[0], period_end=JAN[1], headcount=8, responsible='DRH')
        stats = statistics(Training.objects.all(), list(Employee.objects.all()), scope(basis='training_start'))
        reading = stats['coverage_reference']
        self.assertEqual((reading['numerator'], reading['denominator'], reading['source']), (1, 8, 'drh_reference'))
        self.assertEqual(reading['value'], D('12.50'))
        self.assertEqual(stats['kpi_summary'].metrics['coverage'].denominator, 2)

    def test_certificate_obtained_date_requires_confirmation(self):
        person = Employee.objects.create(number='WF3', name='Synthetic three')
        row = Training(employee=person, title='Curso', start=JAN[0], end=JAN[0], status='Concluído',
                       certificate_obtained=None, certificate_obtained_date=JAN[0])
        with self.assertRaises(ValidationError) as error:
            row.clean()
        self.assertIn('Certificado obtido', ' '.join(error.exception.messages))

from datetime import date
from decimal import Decimal
import csv, io
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Employee, Training
from .dashboard import executive_data


class ExecutiveDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('executive', password='test')
        self.a = Employee.objects.create(number='9336', name='Técnico A', direction='Operações')
        self.b = Employee.objects.create(number='4333', name='Técnico B', direction='Operações')
        self.c = Employee.objects.create(number='100', name='Técnico C', direction='RH')
        self.common = dict(title='Formação', area='Técnica', trainer='Formador', start=date(2026, 1, 2), end=date(2026, 1, 3), days=2, hours_per_day=7)
        self.one = Training.objects.create(employee=self.a, attendance=2, grade=16, result='Aprovado', certified=True, certificate_delivery_date=date(2026, 1, 4), **self.common)
        self.two = Training.objects.create(employee=self.b, attendance=1, grade=8, result='Reprovado', certified=False, **self.common)
        self.planned = Training.objects.create(employee=self.a, attendance=0, status='Planeado', title='Futura', area='Técnica', start=date(2026, 12, 1), end=date(2026, 12, 1), days=1, hours_per_day=7)
        self.old = Training.objects.create(employee=self.a, attendance=1, title='Anterior', area='', start=date(2025, 1, 1), end=date(2025, 1, 1), days=1, hours_per_day=4)
        self.client.force_login(self.user)

    def data(self, **filters):
        response = self.client.get(reverse('dashboard'), filters)
        self.assertEqual(response.status_code, 200)
        return response.context

    def test_counts_do_not_confuse_editions_participations_and_people(self):
        c = self.data()
        self.assertEqual([x['value'] for x in c['kpis'][:5]], [3, 2, 3, 4, Decimal(25)])
        self.assertEqual(c['stats']['attendance'], Decimal('80.0'))
        self.assertEqual(c['kpis'][6]['value'], 1)
        self.assertEqual(c['stats']['certificates_pending'], 2)
        self.assertEqual(c['kpis'][7]['value'], 50)
        self.assertEqual(c['average_final'], 12)
        self.assertAlmostEqual(c['coverage'], 2 / 3 * 100)

    def test_filters_recompute_numerator_and_preserve_staff_denominator(self):
        c = self.data(year='2026', direction='Operações', area='Técnica', status='Concluído')
        self.assertEqual([x['value'] for x in c['kpis'][:5]], [2, 2, 1, 2, Decimal(21)])
        self.assertEqual(c['stats']['attendance'], 75)
        self.assertEqual(len(c['timeline_bars']), 12)
        self.assertEqual(c['timeline_bars'][0]['value'], 21)
        self.assertEqual(c['coverage'], 100)
        self.assertEqual(c['directions_table'][0]['hours'], 21)

    def test_empty_period_and_planned_only_are_not_negative_results(self):
        c = self.data(direction='RH')
        self.assertFalse(c['has_rows'])
        self.assertIsNone(c['stats']['attendance'])
        self.assertIsNone(c['kpis'][7]['value'])
        c = self.data(status='Planeado')
        self.assertIsNone(c['stats']['attendance'])
        self.assertEqual(c['kpis'][1]['value'], 0)
        self.assertEqual(c['kpis'][4]['value'], 0)
        self.assertIsNone(c['average_final'])

    def test_bad_filter_does_not_silently_display_all_training(self):
        c = self.data(year='99999')
        self.assertTrue(c['filter_form'].errors)
        self.assertFalse(c['has_rows'])

    def test_edition_identity_matches_batch_editor(self):
        changed = self.common.copy()
        changed['trainer'] = 'Outro'
        Training.objects.create(employee=self.c, attendance=1, **changed)
        self.assertEqual(self.data()['kpis'][2]['value'], 4)

    def test_csv_uses_same_filters_and_escapes_source_formulas(self):
        self.a.direction = '=1+1'
        self.a.save()
        response = self.client.get(reverse('dashboard'), {'direction': '=1+1', 'export': 'csv'})
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertIn(['Direcção', "'=1+1"], rows)
        self.assertTrue(any(row and row[0] == "'=1+1" and row[1] == '1' for row in rows))

    def test_authentication_required_for_html_and_export(self):
        self.client.logout()
        for query in ({}, {'export': 'csv'}):
            response = self.client.get(reverse('dashboard'), query)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/entrar/', response['Location'])

"""Contract checks across screens and exports, using mixed evidence."""
import csv
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO
from urllib.parse import parse_qs
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from pypdf import PdfReader
from .models import Employee, Training, Course, ExecutiveReport


class SharedKPIIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('kpi-reviewer', password='test')
        self.client.force_login(self.user)
        self.a = Employee.objects.create(number='KA', name='A', direction='Ops', category='Tech', gender='F')
        self.b = Employee.objects.create(number='KB', name='B', direction='Ops', category='Tech', gender='F')
        self.c = Employee.objects.create(number='KC', name='C', direction='RH')
        self.course = Course.objects.create(title='Curso KPI')
        self.make(self.a, attendance=1, hours_per_day=6)
        self.make(self.b, attendance=0, hours_per_day=7)
        self.make(self.a, attendance=1, hours_per_day=20, attendance_confirmed=False)

    def make(self, employee, **changes):
        values = dict(employee=employee, title='Curso KPI', course=self.course,
                      start=date(2026, 1, 30), end=date(2026, 2, 2),
                      days=1, hours_per_day=7, attendance=1, attendance_confirmed=True)
        values.update(changes)
        return Training.objects.create(**values)

    def get(self, route, params=None, args=None):
        response = self.client.get(reverse(route, args=args), params or {})
        self.assertEqual(response.status_code, 200)
        return response

    def test_dashboard_consultation_and_indicators_share_weighted_evidence(self):
        dashboard = self.get('dashboard').context['kpi_summary']
        report = self.get('participation_executive').context['report']['kpi_summary']
        indicators = self.get('indicator_index').context['kpi_summary']
        for summary in (dashboard, report, indicators):
            self.assertEqual(summary.metrics, dashboard.metrics)
            self.assertEqual(summary.metrics['hours'].value, Decimal('6'))
            self.assertEqual(summary.metrics['attendance'].value, Decimal('46.15'))
            self.assertEqual(summary.metrics['coverage'].value, Decimal('33.33'))
            self.assertEqual(summary.metrics['hours'].excluded, 1)
            self.assertEqual(summary.metrics['hours'].status, 'partial')

    def test_profile_print_and_history_preserve_unknown_and_confirmed_zero(self):
        for route in ('employee_data', 'employee_detail', 'employee_print'):
            stats = self.get(route, args=[self.a.pk]).context['stats']
            self.assertEqual(stats['hours'], 6)
            self.assertEqual(stats['kpi_summary'].metrics['hours'].status, 'partial')
            self.assertEqual(stats['kpi_summary'].metrics['coverage'].denominator, 1)
            zero = self.get(route, args=[self.b.pk]).context['stats']
            self.assertEqual(zero['hours'], 0)
            self.assertEqual(zero['attendance'], 0)
            unknown = self.get(route, args=[self.c.pk]).context['stats']
            self.assertIsNone(unknown['hours'])
        self.make(self.a, hours_per_day=None)
        summary = self.get('dashboard').context['kpi_summary']
        self.assertIsNone(summary.metrics['hours_per_person'].value)
        self.assertEqual(summary.metrics['hours'].value, 6)

    def test_common_period_and_person_filters_keep_registry_denominator(self):
        params = {'period': 'month', 'year': '2026', 'month': '1',
                  'time_basis':'training_start', 'course': str(self.course.pk), 'category': 'Tech', 'gender': 'F'}
        dashboard = self.get('dashboard', params).context
        report = self.get('participation_executive', params).context['report']
        self.assertEqual(dashboard['kpi_summary'].metrics, report['kpi_summary'].metrics)
        self.assertEqual(report['universe'], 2)
        self.assertEqual(report['rate'], 50)
        self.assertIsNone(self.get('dashboard', {'year': '2026', 'time_basis':'training_start'}).context['timeline_bars'][1]['value'])
        self.assertEqual(parse_qs(dashboard['query'])['course'], [str(self.course.pk)])
        params['month'] = '2'
        for route in ('dashboard', 'participation_executive'):
            ctx = self.get(route, params).context
            summary = ctx['report']['kpi_summary'] if route == 'participation_executive' else ctx['kpi_summary']
            self.assertIsNone(summary.metrics['hours'].value)
            self.assertEqual(summary.selected_participations, 0)

    def test_exports_share_values_and_visual_search_does_not_trim_excel(self):
        # This fixture contains three people; all aggregate formats must suppress it.
        response=self.get('participation_executive',{'export':'xlsx','q':'no match'})
        book=load_workbook(BytesIO(response.content));self.assertEqual(book.sheetnames,['Protecção'])
        self.assertIn('<5',book.active['A1'].value)
        self.assertIn('<5',self.get('dashboard',{'export':'csv'}).content.decode())
        for route,params in (('participation_executive',{'export':'pdf'}),('executive_preview',{})):
            response=self.get(route,params)
            payload=b''.join(response.streaming_content) if response.streaming else response.content
            text=' '.join(p.extract_text() for p in PdfReader(BytesIO(payload)).pages)
            self.assertIn('<5',text);self.assertNotIn('46.15',text)

    def test_unknown_only_data_stays_unknown_in_exports(self):
        Training.objects.all().update(attendance_confirmed=False)
        summary=self.get('dashboard').context['kpi_summary']
        self.assertIsNone(summary.metrics['hours'].value)
        self.assertEqual(summary.metrics['hours'].status,'to_verify')
        # No unknown values are converted to zero in the protected release.
        book=load_workbook(BytesIO(self.get('participation_executive',{'export':'xlsx'}).content))
        self.assertEqual(book.sheetnames,['Protecção'])

    def test_archive_keeps_structured_evidence_and_course_identity(self):
        response = self.client.post(reverse('executive_create'), {'course': self.course.pk})
        self.assertEqual(response.status_code, 200)
        report = ExecutiveReport.objects.get()
        snapshot = report.summary['kpi']
        self.assertEqual(report.filters['course'], self.course.pk)
        self.assertEqual(snapshot['scope']['filters']['course'], self.course.pk)
        self.assertIsInstance(snapshot['effective_participations'], list)
        self.assertEqual(snapshot['metrics']['attendance']['value'], '46.15')

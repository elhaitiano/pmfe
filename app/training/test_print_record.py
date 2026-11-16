from datetime import date
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Employee, Training


class PrintedRecordTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('reader', password='x-Synthetic-2026', is_staff=True))
        self.person = Employee.objects.create(number='P1', name='Ana Maria Sintética')

    def row(self, **values):
        data = dict(employee=self.person, title='Tratamento de água', area='Técnico', trainer='Formador',
                    start=date(2026, 3, 2), end=date(2026, 3, 3), days=2, hours_per_day=7, attendance=2,
                    attendance_confirmed=True, status='Concluído')
        data.update(values)
        return Training.objects.create(**data)

    def get(self):
        response = self.client.get(reverse('employee_print', args=[self.person.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def test_layout_follows_the_official_sheet(self):
        self.row(certified=True, certificate_issued=True, certificate_delivery_date=date(2026, 3, 10))
        response = self.get()
        for text in ['1. HORAS FREQUENTADAS', '2. CERTIFICADOS', '3. TOTAL FORMAÇÕES', '4. ASSIDUIDADE',
                     'pmf/official-sheet.css', 'pmf/icons/apple-touch-icon.png', 'Ficha PDF', '14 h', '100%']:
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertContains(response, '<col style=', count=7)
        self.assertNotContains(response, 'kpi-evidence')
        self.assertNotContains(response, 'h frequentadas')
        # Only the training title in the Formação column: no certificate state or date below it.
        self.assertNotContains(response, '10/03/2026')
        self.assertNotContains(response, 'Entregue')
        self.assertEqual(response.context['stats']['certificates_reported'], 1)
        self.assertEqual(response.context['stats']['verified_hours_records'], 1)

    def test_without_verified_hours_shows_no_record_not_zero(self):
        self.row(attendance=0, attendance_confirmed=False, certificate_issued=True)
        response = self.get()
        self.assertContains(response, 'Sem registo')
        self.assertNotContains(response, 'Horas de presença sem registo')
        self.assertEqual(response.context['stats']['certificates_reported'], 0)

    def test_empty_history(self):
        self.assertContains(self.get(), 'Sem formações registadas.')

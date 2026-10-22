import json
from datetime import date, time
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Course, Employee, Training, AuditEvent, Enrollment
from .planning import GroupForm, create_group


class ImprovementTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('improvement', is_staff=True))
        self.course = Course.objects.create(title='Curso teste', code='TEST')
        self.values = dict(dates=[date(2025, 1, 1)], trainer='Formador', provider='',
                           location='CD Marçal', capacity=30, hours_per_day=7, break_hours=1,
                           start_time=time(8), end_time=time(16))

    def test_direct_numeric_entry_accepts_decimal_separators_and_keeps_limits(self):
        data = dict(self.values, dates='2025-01-01', capacity='110', hours_per_day='6,5', break_hours='1,0')
        form = GroupForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['capacity'], 110)
        self.assertEqual(form.cleaned_data['hours_per_day'], Decimal('6.5'))
        self.assertEqual(form.fields['hours_per_day'].widget.input_type, 'text')
        self.assertEqual(form.fields['hours_per_day'].widget.attrs['inputmode'], 'decimal')
        self.assertEqual(form.fields['location'].widget.attrs['list'], 'training-sites')
        data['hours_per_day'] = '6.5'
        self.assertTrue(GroupForm(data).is_valid())
        data['capacity'] = '110.5'
        self.assertFalse(GroupForm(data).is_valid())
        data.update(capacity='110', hours_per_day='25')
        self.assertFalse(GroupForm(data).is_valid())

    def test_group_filter_and_historical_source_do_not_create_enrolments(self):
        group = create_group(self.course, self.values)
        other = Course.objects.create(title='Outro curso', code='OTHER')
        create_group(other, self.values)
        response = self.client.get(reverse('planning'), {'course': self.course.pk})
        self.assertEqual([g.pk for g in response.context['page']], [group.pk])
        self.assertContains(response, 'Ver grupos')
        employee = Employee.objects.create(name='Pessoa da fonte', number='IMPROVE')
        training = Training.objects.create(employee=employee, course=self.course, title=self.course.title,
            source_key='pilar3b:TEST-01', start=date(2025,1,1), end=date(2025,1,1), days=1,
            hours_per_day=7, attendance=1, status='Concluído')
        AuditEvent.objects.create(actor='test', action='IMPORT_GROUPS_70_V1', detail=json.dumps({
            'group_id': group.pk, 'source': {'IDs de participação': 'TEST-01'}}))
        response = self.client.get(reverse('group_detail', args=[group.pk]))
        self.assertContains(response, 'Pessoa da fonte')
        self.assertContains(response, 'Formandos do histórico Excel')
        self.assertEqual(Enrollment.objects.count(), 0)
        training.refresh_from_db()
        self.assertIsNone(training.group_id)
        response = self.client.get(reverse('group_create', args=[self.course.pk]))
        for site in ['CD Marçal', 'ETA Kifangondo', 'ETA Kilamba', 'CF de formação da EPAL Kikuxi']:
            self.assertContains(response, site)

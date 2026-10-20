"""Synthetic duration tests; run through test_catalogue_isolated.py."""
import json
from datetime import date, time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import F
from django.test import TestCase
from django.urls import reverse

from .models import AuditEvent, Course, Employee, Training, TrainingGroup
from .planning import CourseForm, GroupForm, create_group


class DurationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('duration-reviewer', is_staff=True)
        self.client.force_login(self.user)
        self.course = Course.objects.create(title='Synthetic duration', default_days=2,
            default_hours=Decimal('7'), source_system='synthetic', source_action_id='SYN-1',
            source_metadata={'days':'2', 'hours':'7'}, source_digest='synthetic-digest')
        self.url = reverse('course_edit', args=[self.course.pk])

    def data(self, **changes):
        values = dict(title=self.course.title, area='', contents='', default_days='2',
            default_hours='7', internal_code='', validation_status='pending',
            validation_notes='', active='on', revision=self.course.revision)
        values.update(changes)
        return values

    def group_data(self, **changes):
        values = dict(dates=[date(2026,1,1), date(2026,1,5)], trainer='Synthetic',
            provider='', location='Synthetic', capacity=30, hours_per_day=Decimal('7'),
            break_hours=Decimal('1'), start_time=time(8), end_time=time(16))
        values.update(changes)
        return values

    def test_decimal_totals_and_missing_factors(self):
        for days, hours, total in [(2,'7','14'), (2,'6','12'), (3,'7','21'),
                (5,'6','30'), (10,'7','70'), (10,'6','60'), (3,'0.55','1.65')]:
            self.assertEqual(Course(default_days=days, default_hours=Decimal(hours)).total_hours,
                             Decimal(total))
        for days, hours in [(None,None), (2,None), (None,Decimal('7'))]:
            self.assertIsNone(Course(default_days=days, default_hours=hours).total_hours)

    def test_server_rejects_invalid_duration_and_forged_totals(self):
        for field, value in [('default_days','0'), ('default_days','-1'),
                ('default_days','1.5'), ('default_hours','0'), ('default_hours','-1'),
                ('default_hours','NaN'), ('default_hours','Infinity'),
                ('default_hours','7.001'), ('total_hours','15'), ('total_hours','NaN')]:
            with self.subTest(field=field, value=value):
                response = self.client.post(self.url, self.data(**{field:value}))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
        self.course.refresh_from_db()
        self.assertEqual(self.course.total_hours, Decimal('14'))
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_correction_requires_both_reason_and_reference(self):
        for extra in [{}, {'duration_reason':'Reason'}, {'duration_reference':'Document'}]:
            self.course.refresh_from_db()
            form = CourseForm(self.data(default_hours='6', **extra), instance=self.course)
            self.assertFalse(form.is_valid())

    def test_correction_is_audited_without_changing_source_or_history(self):
        employee = Employee.objects.create(number='synthetic', name='Synthetic')
        historical = Training.objects.create(employee=employee, title='Legacy',
            start=date(2025,1,1), end=date(2025,1,2), days=2, hours_per_day=7, attendance=1)
        old = Training.objects.values().get(pk=historical.pk)
        response = self.client.post(self.url, self.data(default_hours='6', total_hours='12',
            duration_reason='Documented correction', duration_reference='Synthetic document'))
        self.assertEqual(response.status_code, 302)
        self.course.refresh_from_db()
        self.assertEqual(self.course.total_hours, Decimal('12'))
        self.assertEqual(self.course.validation_status, 'pending')
        self.assertEqual(self.course.source_metadata, {'days':'2','hours':'7'})
        self.assertEqual(self.course.source_digest, 'synthetic-digest')
        self.assertEqual(Training.objects.values().get(pk=historical.pk), old)
        detail = json.loads(AuditEvent.objects.get().detail)
        self.assertEqual(detail['total_hours'], '12')
        self.assertEqual(detail['duration_before'][0], 2)
        self.assertEqual(Decimal(detail['duration_before'][1]), Decimal('7'))
        self.assertEqual(detail['duration_after'], [2,'6'])
        self.assertEqual(detail['reference'], 'Synthetic document')

    def test_nulls_remain_unknown_and_render_without_javascript(self):
        response = self.client.post(self.url, self.data(default_hours='',
            duration_reason='Unknown', duration_reference='Review'))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(self.url)
        self.assertContains(response, 'Por confirmar')
        self.assertContains(response, 'Valores originais preservados')
        self.course.refresh_from_db()
        self.assertIsNone(self.course.total_hours)

    def test_group_service_validates_calendar_total_and_timetable(self):
        self.course.validation_status = 'validated'
        self.course.validation_notes = 'Synthetic approval'
        self.course.save()
        for changes in [dict(hours_per_day=Decimal('NaN')),
                dict(dates=[date(2026,1,1)]*2), dict(total_hours='100'),
                dict(end_time=time(12))]:
            with self.assertRaises(ValidationError):
                create_group(self.course, self.group_data(**changes))
        self.assertEqual(TrainingGroup.objects.count(), 0)
        group = create_group(self.course, self.group_data(total_hours='14'))
        self.assertEqual(group.days, 2)
        self.assertEqual(group.total_hours, Decimal('14'))
        self.assertEqual(group.sessions.count(), 2)
        Course.objects.filter(pk=self.course.pk).update(default_hours=Decimal('6'))
        group.refresh_from_db()
        self.assertEqual(group.total_hours, Decimal('14'))

    def test_group_form_uses_actual_dates_not_calendar_span(self):
        form = GroupForm({**self.group_data(), 'dates':'2026-01-01\n2026-01-05'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.total_hours, Decimal('14'))
        unbound = GroupForm(initial={'dates':'2026-01-01\n2026-01-05', 'hours_per_day':7})
        self.assertEqual(unbound.total_hours, Decimal('14'))

    def test_stale_revision_does_not_save_or_audit(self):
        Course.objects.filter(pk=self.course.pk).update(revision=F('revision')+1)
        response = self.client.post(self.url, self.data(default_hours='6',
            duration_reason='Reason', duration_reference='Reference'))
        self.assertEqual(response.status_code, 200)
        self.course.refresh_from_db()
        self.assertEqual(self.course.default_hours, Decimal('7'))
        self.assertFalse(AuditEvent.objects.exists())

    def test_failed_audit_rolls_back_duration_update(self):
        with patch('training.planning.record', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.data(default_hours='6',
                    duration_reason='Reason', duration_reference='Reference'))
        self.course.refresh_from_db()
        self.assertEqual(self.course.default_hours, Decimal('7'))

    def test_server_renders_total_and_unknown_in_cards(self):
        response = self.client.get(reverse('planning'))
        self.assertContains(response, '14 H')
        response = self.client.get(self.url)
        self.assertContains(response, '14 H')
        self.assertContains(response, 'planTotalHours')

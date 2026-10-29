from unittest.mock import patch
import json
from datetime import date, time
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Course, Employee, Training, AuditEvent, Enrollment, Trainer
from .planning import GroupForm, create_group


class ImprovementTests(TestCase):
    def setUp(self):
        clock = patch('training.planning.timezone.localdate', return_value=date(2025, 1, 1))
        clock.start()
        self.addCleanup(clock.stop)
        self.client.force_login(get_user_model().objects.create_user('improvement', is_staff=True))
        self.course = Course.objects.create(title='Curso teste', code='TEST')
        self.values = dict(dates=[date(2025, 1, 1)], trainer='Formador', provider='',
                           location='CD Marçal', capacity=30, hours_per_day=7, break_hours=1,
                           start_time=time(8), end_time=time(16))

    def test_direct_numeric_entry_accepts_decimal_separators_and_keeps_limits(self):
        data = dict(self.values, start='2025-01-01', end='2025-01-01', capacity='110', hours_per_day='6,5', break_hours='1,0')
        data['trainer'] = Trainer.objects.create(name='Formador').pk
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

    def test_group_creation_selects_registered_trainer_and_opens_attendance_preparation(self):
        trainer = Trainer.objects.create(name='Formador registado', active=True)
        inactive = Trainer.objects.create(name='Inactivo', active=False)
        data = dict(self.values, start='2025-01-01', end='2025-01-01', trainer=trainer.pk, session_dates='2025-01-01', action='prepare_attendance')
        response = self.client.post(reverse('group_create', args=[self.course.pk]), data)
        group = self.course.groups.get()
        self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
        self.assertEqual(group.trainer_record_id, trainer.pk)
        self.assertEqual(group.trainer, trainer.name)
        self.assertEqual(group.sessions.count(), 1)
        self.assertFalse(group.enrollments.exists())
        data['trainer'] = inactive.pk
        self.assertFalse(GroupForm(data).is_valid())
        trainer.active = False
        trainer.save()
        self.assertTrue(GroupForm(dict(data, trainer=trainer.pk), current_trainer=trainer.pk).is_valid())
        response = self.client.get(reverse('group_edit', args=[group.pk]))
        self.assertNotContains(response, 'Gerir formadores')
        self.assertNotContains(response, 'prepare_attendance')
        self.assertEqual(response.context['form'].initial['trainer'], trainer.pk)

    def test_attendance_preparation_is_on_saved_group_and_closes_after_end(self):
        from .models import Enrollment
        group = create_group(self.course, self.values)
        self.assertContains(self.client.get(reverse('group_detail', args=[group.pk])), 'Seleccionar formandos para a lista')
        person = Employee.objects.create(name='New participant', number='NEW')
        Enrollment.objects.create(employee=person, group=group)
        url = reverse('attendance_excel', args=[group.pk])
        self.assertEqual(self.client.get(url, {'date': '2025-01-01'}).status_code, 200)
        with patch('training.planning.timezone.localdate', return_value=date(2025, 1, 2)):
            response = self.client.get(reverse('group_detail', args=[group.pk]))
            self.assertNotContains(response, 'Seleccionar formandos para a lista')
            self.assertContains(response, 'Segunda via / consulta.')
            self.assertEqual(self.client.get(url, {'date': '2025-01-01'}).status_code, 200)
            self.assertRedirects(self.client.get(reverse('group_enrol', args=[group.pk])), reverse('group_detail', args=[group.pk]))

    def test_candidate_page_only_shows_eligible_learners(self):
        group = create_group(self.course, self.values)
        eligible = Employee.objects.create(name='Available person', number='ELIGIBLE')
        previous = Employee.objects.create(name='Already trained', number='PREVIOUS')
        Training.objects.create(employee=previous, course=self.course, title=self.course.title,
            start=date(2024,1,1), end=date(2024,1,1), days=1, hours_per_day=7, attendance=1)
        response = self.client.get(reverse('group_enrol', args=[group.pk]))
        self.assertContains(response, eligible.name)
        self.assertNotContains(response, previous.name)

    def test_explicit_days_preserved_and_posted_duration_ignored(self):
        trainer = Trainer.objects.create(name='Calendar trainer', active=True)
        data = dict(self.values, trainer=trainer.pk, start='28/02/2028', end='01/03/2028',
                    dates='2099-01-01', days='999', session_dates='2028-02-28\n2028-02-29\n2028-03-01')
        form = GroupForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['dates'], [date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1)])
        group = create_group(self.course, form.cleaned_data)
        self.assertEqual(group.days, 3)
        self.assertEqual(group.start, date(2028, 2, 28))
        self.assertEqual(group.end, date(2028, 3, 1))
        self.assertEqual(list(group.sessions.values_list('date', flat=True)), form.cleaned_data['dates'])
        same_day = GroupForm(dict(data, end='2028-02-28', session_dates='2028-02-28'))
        self.assertTrue(same_day.is_valid(), same_day.errors)
        self.assertEqual(len(same_day.cleaned_data['dates']), 1)
        self.assertFalse(GroupForm(dict(data, end='2028-02-27')).is_valid())
        self.assertFalse(GroupForm(dict(data, end='2030-01-01')).is_valid())
        self.assertFalse(GroupForm(dict(data, start='')).is_valid())
        response = self.client.get(reverse('group_edit', args=[group.pk]))
        self.assertContains(response, 'value="28/02/2028"')
        self.assertContains(response, 'placeholder="dd/mm/aaaa"')
        self.assertFalse(GroupForm(dict(data, end='31/02/2028')).is_valid())
        self.assertContains(response, 'id="planDuration"')
        self.assertNotContains(response, 'id="id_dates"')
        self.assertEqual(response.context['form'].initial['start'], group.start)

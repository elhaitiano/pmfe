from datetime import date, time
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from .analytics import statistics, distribution
from .forms import TrainingForm
from .models import Course, Employee, Enrollment, Training
from .planning import create_group


class TrainingIntegrityTests(TestCase):
    def setUp(self):
        self.person = Employee.objects.create(number='INT01', name='Pessoa de teste')
        self.other = Employee.objects.create(number='INT02', name='Outra pessoa')
        self.course = Course.objects.create(title='Curso de teste', code='INT')
        self.group = create_group(self.course, dict(dates=[date(2025, 1, 1)], trainer='Formador',
            provider='', location='Sala', capacity=10, hours_per_day=Decimal('7'),
            break_hours=Decimal('1'), start_time=time(8), end_time=time(16)))
        self.user = get_user_model().objects.create_user('editor_integrity', is_staff=True)
        self.client.force_login(self.user)

    def training(self, **changes):
        data = dict(employee=self.person, title='Histórico', start=date(2025, 1, 1),
            end=date(2025, 1, 1), days=1, hours_per_day=7, attendance=0, status='Concluído')
        data.update(changes)
        return Training(**data)

    def test_link_rejects_wrong_employee_and_wrong_group(self):
        en = Enrollment.objects.create(group=self.group, employee=self.person)
        row = self.training(group=self.group, course=self.course)
        row.full_clean(); row.save()
        en.training = row; en.save()
        wrong = Enrollment(group=self.group, employee=self.other, training=row)
        with self.assertRaises(ValidationError):
            wrong.save()
        row.employee = self.other
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.full_clean()
        legacy = self.training(); legacy.save()
        en.training = legacy
        with self.assertRaises(ValidationError):
            en.save()

    def test_group_participation_unique_but_legacy_history_is_not_merged(self):
        Enrollment.objects.create(group=self.group, employee=self.person)
        row = self.training(group=self.group); row.full_clean(); row.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.training(group=self.group).save()
        self.training(source_key='pilar3b:PF-01').save()
        self.training(source_key='pilar3b:PF-02').save()
        self.assertEqual(Training.objects.filter(group=None).count(), 2)

    def test_start_links_once_and_finish_requires_participation(self):
        en = Enrollment.objects.create(group=self.group, employee=self.person)
        response = self.client.post(reverse('group_transition', args=[self.group.pk]),
            {'revision': self.group.revision, 'action': 'start'})
        self.assertEqual(response.status_code, 302)
        en.refresh_from_db()
        self.assertEqual(en.training.course_id, self.course.pk)
        self.assertEqual(en.training.plan_status, 'No Plano')
        self.assertEqual(en.training.status, 'Em Curso')
        self.assertFalse(en.training.attendance_confirmed)
        self.client.post(reverse('group_transition', args=[self.group.pk]),
            {'revision': self.group.revision, 'action': 'start'})
        self.assertEqual(Training.objects.count(), 1)

    def test_finish_rejects_unlinked_enrolment(self):
        Enrollment.objects.create(group=self.group, employee=self.person)
        self.group.state = 'Em Curso'; self.group.save(update_fields=['state'])
        self.client.post(reverse('group_transition', args=[self.group.pk]),
            {'revision': self.group.revision, 'action': 'finish'})
        self.group.refresh_from_db()
        self.assertEqual(self.group.state, 'Em Curso')
        self.assertEqual(self.group.revision, 1)

    def test_existing_participation_prevents_duplicate_start_atomically(self):
        Enrollment.objects.create(group=self.group, employee=self.person)
        self.training(group=self.group).save()
        self.client.post(reverse('group_transition', args=[self.group.pk]),
            {'revision': self.group.revision, 'action': 'start'})
        self.group.refresh_from_db()
        self.assertEqual(self.group.state, 'Planeada')
        self.assertEqual(self.group.revision, 1)
        self.assertEqual(Training.objects.count(), 1)

    def test_unknown_duration_and_zero_are_not_invented(self):
        row = self.training(days=None, hours_per_day=None, source_key='pilar3b:PF-99')
        row.full_clean(); row.save()
        self.assertEqual(row.participation_id, 'PF-99')
        self.assertIsNone(row.completed_hours)
        self.assertIsNone(row.planned_hours)
        self.assertIsNone(row.absences)
        self.assertIsNone(row.attendance_percent)
        data = statistics([row])
        self.assertEqual(data['unknown_duration_count'], 1)
        self.assertIsNone(data['attendance'])
        self.assertEqual(distribution([row]), [])
        row.days = 2; row.hours_per_day = 7
        self.assertIsNone(row.attendance_percent)
        row.attendance_confirmed = True
        self.assertEqual(row.attendance_percent, 0)
        self.assertEqual(row.absences, 2)
        row.source_key = 'pilar3b:PF-OTHER'
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_history_form_and_reports_allow_unknown_duration(self):
        payload = dict(title='Histórico desconhecido', start='2025-01-01', end='2025-01-01',
            days='', hours_per_day='', attendance=0, status='Concluído', result='Pendente',
            certified='unknown', plan_status='Fora do Plano', historical_name='Nome antigo',
            identity_quality='Rever', matching_method='Correspondência manual', source_row=12,
            source_course_code='OLD')
        form = TrainingForm(data=payload, instance=Training(employee=self.person))
        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertIsNone(row.group_id)
        self.assertEqual(row.plan_status, 'Fora do Plano')
        for route, args in [('employee_detail', [self.person.pk]), ('employee_print', [self.person.pk]),
                            ('employee_data', [self.person.pk]), ('dashboard', []), ('executive_explore', [])]:
            response = self.client.get(reverse(route, args=args))
            self.assertEqual(response.status_code, 200, route)
        response = self.client.get(reverse('employee_detail', args=[self.person.pk]))
        self.assertContains(response, 'Nome antigo')
        self.assertContains(response, 'Fora do Plano')
        response = self.client.get(reverse('executive_preview'), {'period': 'year', 'year': '2025'})
        self.assertEqual(response.status_code, 200)
        content = b''.join(response.streaming_content) if response.streaming else response.content
        self.assertTrue(content.startswith(b'%PDF-'))
        response.close()

    def test_course_link_and_unknown_group_duration_are_validated(self):
        Enrollment.objects.create(group=self.group, employee=self.person)
        other_course = Course.objects.create(title='Outro curso')
        row = self.training(group=self.group, course=other_course)
        with self.assertRaises(ValidationError): row.full_clean()
        row.course = self.course; row.days = None
        with self.assertRaises(ValidationError): row.full_clean()


class HistoryMigrationTests(TransactionTestCase):
    def test_preserves_source_identifiers_and_backfills_trace(self):
        before = [('training', '0014_course_code')]
        after = [('training', '0015_training_history_integrity')]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            employee = apps.get_model('training', 'Employee').objects.create(number='M1', name='Teste')
            source = {'Nome Histórico Pilar 3B': 'Nome anterior', 'Qualidade da Identidade': 'Reconciliada',
                'Método de Correspondência': 'Alias', 'Linha Fonte Pilar 3B': '24',
                'Código Formação': 'ABC', 'Status do Plano': 'No Plano', 'Extra': 'preservar'}
            apps.get_model('training', 'Training').objects.create(employee=employee, title='Histórico',
                start=date(2025,1,1), end=date(2025,1,1), days=1, hours_per_day=7,
                attendance=0, source_key='pilar3b:PF-100', source_data=source)
            executor = MigrationExecutor(connection); executor.migrate(after)
            Model = executor.loader.project_state(after).apps.get_model('training', 'Training')
            row = Model.objects.get(source_key='pilar3b:PF-100')
            self.assertEqual(row.source_data, source)
            self.assertEqual(row.historical_name, 'Nome anterior')
            self.assertEqual(row.source_row, 24)
            self.assertEqual(row.plan_status, 'No Plano')
            self.assertIsNone(row.group_id)
            self.assertIsNone(row.course_id)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

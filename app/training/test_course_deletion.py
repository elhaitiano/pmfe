from datetime import date, time
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import AuditEvent, Course, Employee, Training
from .planning import create_group, course_deletion_allowed


class CourseDeletionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('catalogue-admin', is_staff=True)
        self.reader = get_user_model().objects.create_user('catalogue-reader')
        self.course = Course.objects.create(validation_status='validated', validation_notes='Synthetic approved fixture', title='Sécurité des installations')
        self.employee = Employee.objects.create(number='001', name='Technicien test')
        self.url = reverse('course_delete', args=[self.course.pk])
        self.client.force_login(self.admin)

    def payload(self, **changes):
        return {'revision': self.course.revision, 'confirm': 'ELIMINAR',
                'reason': 'Création de test', **changes}

    def history(self, title):
        return Training.objects.create(employee=self.employee, title=title,
            start=date(2025, 1, 1), end=date(2025, 1, 1), days=1,
            hours_per_day=7, attendance=1, status='Concluído')

    def test_deletion_requires_staff_and_confirmation(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.assertNotContains(self.client.get(reverse('planning')), 'Eliminar formação')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('planning')), self.url)
        self.client.get(self.url)
        for change in [{'confirm': ''}, {'reason': ' '}, {'reason': 'x' * 1001},
                       {'revision': ''}, {'revision': '999999999999999999999'}]:
            with self.subTest(change=next(iter(change))):
                self.client.post(self.url, self.payload(**change))
                self.course.refresh_from_db()
                self.assertEqual(self.course.revision, 1)
        self.assertFalse(AuditEvent.objects.filter(action='COURSE_DELETE').exists())

    def test_unused_course_deleted_with_audit_and_other_data_preserved(self):
        other = Course.objects.create(validation_status='validated', validation_notes='Synthetic approved fixture', title='Autre formation')
        training = self.history(other.title)
        response = self.client.post(self.url, self.payload())
        self.assertRedirects(response, reverse('planning'))
        self.assertFalse(Course.objects.filter(pk=self.course.pk).exists())
        self.assertTrue(Course.objects.filter(pk=other.pk).exists())
        self.assertTrue(Training.objects.filter(pk=training.pk).exists())
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        audit = AuditEvent.objects.get(action='COURSE_DELETE')
        self.assertIn('Création de test', audit.detail)
        self.assertIn(self.course.title, audit.detail)

    def test_any_group_blocks_course_deletion_even_when_cancelled(self):
        group = create_group(self.course, dict(dates=[date(2026, 1, 1)],
            trainer='Formateur', provider='', location='Salle', capacity=10,
            hours_per_day=7, break_hours=1, start_time=time(8), end_time=time(16)))
        for state in ['Planeada', 'Cancelada', 'Em Curso', 'Concluída']:
            group.state = state
            group.save(update_fields=['state'])
            self.client.post(self.url, self.payload())
            self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())
            self.assertTrue(group.sessions.exists())
        self.assertFalse(AuditEvent.objects.filter(action='COURSE_DELETE').exists())

    def test_history_title_and_previous_title_block_deletion(self):
        for title in ['  SÉCURITÉ   DES INSTALLATIONS ', 'Ancien intitulé']:
            self.course.aliases = ['Ancien intitulé']
            self.course.save()
            training = self.history(title)
            with self.subTest(title=title):
                page = self.client.get(self.url)
                self.assertNotContains(page, 'name="confirm"')
                self.client.post(self.url, self.payload())
                self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())
                self.assertTrue(Training.objects.filter(pk=training.pk).exists())
            training.delete()

    def test_history_added_after_confirmation_page_is_checked_again(self):
        self.assertContains(self.client.get(self.url), 'name="confirm"')
        training = self.history(self.course.title)
        self.client.post(self.url, self.payload())
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())
        self.assertTrue(Training.objects.filter(pk=training.pk).exists())

    def test_stale_confirmation_does_not_delete_updated_course(self):
        self.course.revision += 1
        self.course.save()
        self.client.post(self.url, self.payload(revision=1))
        self.course.refresh_from_db()
        self.assertEqual(self.course.revision, 2)
        self.assertFalse(AuditEvent.objects.filter(action='COURSE_DELETE').exists())

    def test_imported_action_is_protected_in_cards_confirmation_and_direct_posts(self):
        self.course.source_system = 'synthetic-catalogue'
        self.course.source_action_id = 'AC-SYN-001'
        self.course.save()
        before = Course.objects.get(pk=self.course.pk).__dict__.copy()
        before.pop('_state')
        self.assertFalse(course_deletion_allowed(self.course))
        self.assertNotContains(self.client.get(reverse('planning')), self.url)
        page = self.client.get(self.url)
        self.assertContains(page, 'Acção importada protegida')
        self.assertContains(page, 'Editar acção')
        self.assertNotContains(page, 'name="confirm"')
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 403)
        after = Course.objects.get(pk=self.course.pk).__dict__.copy()
        after.pop('_state')
        self.assertEqual(after, before)
        self.assertFalse(AuditEvent.objects.filter(action='COURSE_DELETE').exists())

    def test_either_origin_field_protects_the_helper_without_database_access(self):
        for system,identifier in [('synthetic',''), ('','AC-SYN-001')]:
            self.course.source_system, self.course.source_action_id = system,identifier
            with self.subTest(system=system), self.assertNumQueries(0):
                self.assertFalse(course_deletion_allowed(self.course))

    def test_origin_added_after_confirmation_blocks_deletion_without_revision_change(self):
        self.assertContains(self.client.get(self.url), 'name="confirm"')
        Course.objects.filter(pk=self.course.pk).update(source_system='synthetic',source_action_id='AC-SYN-001')
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 403)
        self.course.refresh_from_db()
        self.assertEqual(self.course.revision, 1)
        self.assertFalse(AuditEvent.objects.filter(action='COURSE_DELETE').exists())

from datetime import date, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from .models import Course, Employee, Trainer, Training
from .planning import create_group


class TrainerRegistryTests(TestCase):
    def test_homonyms_and_blank_name_validation(self):
        Trainer.objects.create(name='Maria Silva', organization='Entidade A')
        other = Trainer.objects.create(name='Maria Silva', organization='Entidade B')
        self.assertEqual(Trainer.objects.count(), 2)
        self.assertEqual(str(other), 'Maria Silva — Entidade B')
        with self.assertRaises(ValidationError):
            Trainer(name='   ').full_clean()

    def test_links_preserve_historical_names_and_protect_registry(self):
        trainer = Trainer.objects.create(name='Nome actual')
        employee = Employee.objects.create(number='T-1', name='Participante')
        training = Training.objects.create(
            employee=employee, title='Curso', trainer='Nome histórico',
            trainer_record=trainer, start=date(2026, 1, 1), end=date(2026, 1, 1),
            days=1, hours_per_day=7, attendance=0,
        )
        trainer.name = 'Nome actualizado'
        trainer.save()
        training.refresh_from_db()
        self.assertEqual(training.trainer, 'Nome histórico')
        self.assertEqual(training.trainer_record.name, 'Nome actualizado')
        with self.assertRaises(ProtectedError):
            trainer.delete()
        training.trainer_record = None
        training.save()
        trainer.delete()
        training.refresh_from_db()
        self.assertEqual(training.trainer, 'Nome histórico')

    def test_group_can_link_registry_without_changing_existing_workflow(self):
        trainer = Trainer.objects.create(name='Formador')
        course = Course.objects.create(code='T-1', title='Curso teste', area='Técnica')
        group = create_group(course, dict(
            dates=[date(2026, 1, 1)], trainer='Nome histórico', provider='EPAL',
            location='Sala', capacity=20, hours_per_day=Decimal('7'),
            break_hours=Decimal('1'), start_time=time(8), end_time=time(16),
        ))
        self.assertIsNone(group.trainer_record_id)
        group.trainer_record = trainer
        group.save()
        with self.assertRaises(ProtectedError):
            trainer.delete()
        self.assertEqual(group.trainer, 'Nome histórico')


class TrainerViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.staff = get_user_model().objects.create_user('trainer-manager', is_staff=True)
        self.reader = get_user_model().objects.create_user('trainer-reader')

    def test_access_csrf_and_registry_workflow(self):
        from django.test import Client
        from django.urls import reverse
        from .models import AuditEvent
        listing = reverse('trainer_index')
        create = reverse('trainer_create')
        self.assertEqual(self.client.get(listing).status_code, 302)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(listing).status_code, 403)
        self.assertEqual(self.client.post(create, {'name': 'Forbidden'}).status_code, 403)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(listing), 'Nenhum formador registado')
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(create, {'name': 'No CSRF'}).status_code, 403)
        self.assertEqual(self.client.post(create, {'name': '', 'email': 'invalid'}).status_code, 200)
        self.assertEqual(Trainer.objects.count(), 0)
        response = self.client.post(create, {'name': 'Ana Teste', 'organization': 'EPAL', 'active': 'on'})
        self.assertRedirects(response, listing)
        trainer = Trainer.objects.get()
        self.assertTrue(trainer.active)
        self.assertContains(self.client.get(listing, {'q': 'Ana'}), 'Ana Teste')
        self.assertNotContains(self.client.get(listing, {'q': 'Absent'}), 'Ana Teste')
        response = self.client.post(reverse('trainer_edit', args=[trainer.pk]), {
            'name': 'Ana Teste', 'organization': 'EPAL', 'email': 'ana@example.com',
        })
        self.assertRedirects(response, listing)
        trainer.refresh_from_db()
        self.assertFalse(trainer.active)
        self.assertEqual(AuditEvent.objects.filter(action__in=['TRAINER_CREATE', 'TRAINER_UPDATE']).count(), 2)
        self.assertContains(self.client.get(reverse('planning')), 'Formadores')
        self.assertEqual(self.client.get(reverse('trainer_edit', args=[99999])).status_code, 404)

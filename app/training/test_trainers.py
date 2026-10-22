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

from datetime import date, time
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Course, Trainer, Training, Employee, PrivateDocument, TrainingCentre
from .planning import GroupForm, DEFAULT_LOCATIONS
from .scheduling import save_group
from .documents import CertificateForm


class SimplifiedWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('workflow', is_staff=True)
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(Permission.objects.get(codename='view_training'))
        self.client.force_login(self.user)
        self.trainer = Trainer.objects.create(name='Formador de teste')
        self.course = Course.objects.create(validation_status='validated', title='Curso de teste')
        self.values = dict(dates=[date(2027, 1, 4)], start=date(2027, 1, 4), end=date(2027, 1, 4), trainer=self.trainer,
            provider='', location=DEFAULT_LOCATIONS[0], capacity=20, hours_per_day=Decimal('7'), break_hours=Decimal('1'),
            start_time=time(8), end_time=time(16), planning_confirmation='Confirmado', centre=None, room=None)

    def test_confirmed_group_without_room_and_next_number(self):
        group = save_group(self.course.pk, self.values)
        self.assertEqual(group.code, 'G1')
        self.assertIsNone(group.room_id)
        self.assertEqual(group.centre.name, self.values['location'])
        self.assertEqual(save_group(self.course.pk, {**self.values, 'dates': [date(2027, 1, 5)], 'start': date(2027, 1, 5), 'end': date(2027, 1, 5)}).code, 'G2')
        self.assertEqual(TrainingCentre.objects.count(), 1)
        self.assertEqual(Training.objects.count(), 0)

    def test_new_location_requires_name_and_preserves_sessions(self):
        data = {**self.values, 'trainer': self.trainer.pk, 'start': '2027-01-04', 'end': '2027-01-04', 'session_dates': '2027-01-04', 'location': '__new__'}
        self.assertFalse(GroupForm(data).is_valid())
        form = GroupForm({**data, 'new_location': 'Novo Centro — Luanda'})
        self.assertTrue(form.is_valid(), form.errors)
        group = save_group(self.course.pk, form.cleaned_data)
        self.assertEqual(group.location, 'Novo Centro — Luanda')
        self.assertEqual(group.sessions.count(), 1)
        self.assertIn(group.location, str(GroupForm()['location']))

    def test_archive_is_document_based_not_completion_or_hr_flag(self):
        person = Employee.objects.create(number='TEST-1', name='Pessoa de teste')
        t = Training.objects.create(employee=person, title='Curso', start=date(2025, 1, 1), end=date(2025, 1, 2), days=2, hours_per_day=7, attendance=0, status='Concluído')
        self.assertEqual(t.certificate_archive_label, 'Por arquivar')
        self.assertContains(self.client.get(reverse('certificate_archive')), 'Por arquivar')
        doc = PrivateDocument.objects.create(employee=person, training=t, kind='certificate', original_name='cert.pdf', sha256='a'*64, size=10, actor='test')
        self.assertEqual(t.certificate_archive_label, 'Arquivado')
        self.assertContains(self.client.get(reverse('certificate_archive') + '?state=archived'), person.name)
        doc.active = False
        doc.save()
        self.assertEqual(t.certificate_archive_label, 'Por arquivar')
        form = CertificateForm(instance=t)
        self.assertNotIn('certificate_hr_delivered', form.fields)
        self.assertNotIn('certificate_hr_delivery_date', form.fields)
        self.assertIsNone(t.certified)
        self.assertFalse(t.attendance_confirmed)

    def test_archive_requires_staff(self):
        self.user.is_staff = False
        self.user.save()
        self.assertIn(self.client.get(reverse('certificate_archive')).status_code, (302, 403))

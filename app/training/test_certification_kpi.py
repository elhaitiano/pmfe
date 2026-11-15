from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from .models import Employee,Training,CertificateEvent,Course,TrainingGroup,Enrollment
from .certificates import summary,validate_transition
from .documents import CertificateForm

class CertificationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('cert-admin',is_staff=True)
        self.client.force_login(self.user)
        self.employee=Employee.objects.create(number='CERT1',name='Pessoa fictícia')
        self.row=Training.objects.create(employee=self.employee,title='Curso',start=date(2025,1,1),end=date(2025,1,2),days=2,hours_per_day=7,attendance=0)
    def payload(self,**changes):
        data=dict(revision=self.row.version,certificate_obtained='true',certificate_obtained_date='2025-01-02',certificate_issued='true',certificate_issue_date='2025-02-05',certified='true',certificate_delivery_date='2025-03-06',certificate_hr_delivered='unknown',certificate_actor='DRH',certificate_evidence='Registo administrativo 1',reason='Confirmação conjunta explícita')
        data.update(changes);return data
    def test_three_independent_states_audit_without_upload(self):
        self.assertEqual(self.client.post(reverse('certificate_edit',args=[self.row.pk]),self.payload()).status_code,302)
        self.row.refresh_from_db();event=CertificateEvent.objects.get()
        self.assertIsNone(event.before['certificate_obtained']);self.assertTrue(event.after['certified'])
        self.assertEqual(event.actor,self.user.username)
        # Attendance and academic decisions are not inferred.
        self.assertEqual(self.row.attendance,0);self.assertIsNone(self.row.grade)
    def test_sequence_dates_and_provenance_are_required(self):
        for change in [dict(certificate_obtained='unknown',certificate_obtained_date=''),dict(certificate_issued='unknown',certificate_issue_date=''),dict(certificate_issue_date='2025-01-01'),dict(certificate_delivery_date='2025-01-03'),dict(certificate_actor=''),dict(certificate_evidence=''),dict(reason='')]:
            with self.subTest(change=change):
                self.assertFalse(CertificateForm(self.payload(**change),instance=Training.objects.get(pk=self.row.pk)).is_valid())
    def test_unknown_negative_and_incompatible_legacy_not_backfilled(self):
        Training.objects.filter(pk=self.row.pk).update(certified=True)
        self.row.refresh_from_db();self.row.observations='Correction unrelated';self.row.full_clean()
        self.assertIsNone(self.row.certificate_obtained)
        result=summary([self.row]);self.assertEqual(result['obtained_unknown'],1);self.assertIsNone(result['delivery_rate'])
        self.assertIn('inactivo',result['classification'])
    def test_cohort_rates_and_event_months_are_separate(self):
        form=CertificateForm(self.payload(),instance=self.row);self.assertTrue(form.is_valid(),form.errors);form.save()
        jan=summary([self.row],date(2025,1,31),date(2025,1,1))
        self.assertEqual(jan['obtained'],1);self.assertEqual(jan['issued'],0);self.assertEqual(jan['issue_rate'],0)
        feb=summary([self.row],date(2025,2,28),date(2025,2,1))
        self.assertEqual(feb['issue_events'],1);self.assertEqual(feb['obtained'],0);self.assertIsNone(feb['issue_rate'])
        mar=summary([self.row],date(2025,3,31),date(2025,1,1))
        self.assertEqual(mar['delivery_rate'],100);self.assertEqual(mar['delivery_people'],1)
        monthly=summary([self.row],date(2025,3,31),date(2025,3,1))
        self.assertEqual(monthly['delivery_events'],1);self.assertIsNone(monthly['delivery_rate'])
    def test_stale_and_nonstaff_cannot_confirm(self):
        self.assertEqual(self.client.post(reverse('certificate_edit',args=[self.row.pk]),self.payload(revision=99)).status_code,200)
        self.assertFalse(CertificateEvent.objects.exists())
        self.user.is_staff=False;self.user.save()
        self.assertEqual(self.client.post(reverse('certificate_edit',args=[self.row.pk]),self.payload()).status_code,403)
    def test_group_review_tampering_and_atomic_confirmation(self):
        course=Course.objects.create(title='Curso');group=TrainingGroup.objects.create(course=course,number=1,start_time="08:00",end_time="15:00",start=self.row.start,end=self.row.end,days=2,hours_per_day=7)
        self.row.group=group;self.row.course=course;self.row.save()
        Enrollment.objects.create(group=group,employee=self.employee,training=self.row,state='Activa')
        url=reverse('group_certificates',args=[group.pk]);data=self.payload(participants=[self.row.pk],action='review')
        preview=self.client.post(url,data);self.assertTrue(preview.context['token'],preview.context['form'].errors)
        data.update(action='confirm',token=preview.context['token'],certificate_declaration='Tampered',certificate_declared_by='DRH',certificate_declaration_source='New')
        self.client.post(url,data);self.assertFalse(CertificateEvent.objects.exists())
        data.pop('certificate_declaration');data.pop('certificate_declared_by');data.pop('certificate_declaration_source')
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(CertificateEvent.objects.count(),1)
        self.client.post(url,data);self.assertEqual(CertificateEvent.objects.count(),1)


from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

class CertificationMigrationTests(TransactionTestCase):
    def test_legacy_flags_and_sources_preserved_obtention_not_inferred(self):
        before=[('training','0027_attendance_import_audit')]
        executor=MigrationExecutor(connection);executor.migrate(before)
        try:
            apps=executor.loader.project_state(before).apps
            person=apps.get_model('training','Employee').objects.create(number='LEGACY',name='Synthetic legacy')
            row=apps.get_model('training','Training').objects.create(employee=person,title='Legacy',start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1,certified=True,certificate_issued=True,source_data={'state':'ambiguous'})
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
            current=Training.objects.get(pk=row.pk)
            self.assertTrue(current.certified);self.assertTrue(current.certificate_issued)
            self.assertIsNone(current.certificate_obtained);self.assertIsNone(current.certificate_obtained_date)
            self.assertEqual(current.source_data,{'state':'ambiguous'})
            self.assertFalse(CertificateEvent.objects.exists())
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())

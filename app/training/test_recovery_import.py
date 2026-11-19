from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError
from .models import Course,Employee,Trainer,TrainingGroup,Training,Enrollment,DocumentaryRecord,AuditEvent,PrivateDocument
from .recovery_import import apply,approved_undated_receipt

@override_settings(PMF_VALIDATION_ENABLED=False,PMF_HISTORICAL_ENABLED=False,PMF_SIMPLIFIED_PLANNING_ENABLED=True)
class RecoveryImportTests(TestCase):
    def setUp(self):
        self.course=Course.objects.create(title='Curso de ensaio',validation_status='pending')
        self.trainer=Trainer.objects.create(name='Formador fictício',active=True)
        self.a=Employee.objects.create(number='QA-R1',name='Pessoa A')
        self.b=Employee.objects.create(number='QA-R2',name='Pessoa B',phone='924222222')
        self.c=Employee.objects.create(number='QA-R3',name='Pessoa C')
        def row(e,marks,cert=False,received=False,contact=None):
            return dict(number=e.number,registry_name=e.name,confirmed_name=e.name,source_name=e.name,marks=marks,obtained=True if received else None,
                delivered=True if received else None,delivery_confirmation='Confirmação expressa de ensaio' if received else None,
                certificate=dict(sha256='a'*64,issue_date='2026-01-03',course_hours='14',file='ensaio.pdf') if cert else None,
                source=dict(file='ensaio.xlsx',sheet='G1',row=8),contact_change=contact)
        g1=dict(course=self.course.pk,number=1,code='QA',trainer=self.trainer.pk,location='Local fictício',provider='',
            dates=['2026-01-02','2026-01-03'],hours_per_day='7',break_hours='1',start_time='08:30',end_time='16:30',capacity=30,
            rows=[row(self.a,['P','P'],True,contact=dict(field='phone',before='',after='923111111')),
                row(self.b,['A','P'],True,True,dict(field='source_data.Telefone 2',before='',after='925333333'))])
        g2=dict(course=self.course.pk,number=2,code='QA',trainer=self.trainer.pk,location='Local fictício',provider='',dates=['2026-01-05'],
            hours_per_day='7',break_hours='1',start_time='08:30',end_time='16:30',capacity=30,rows=[row(self.c,['P'])])
        self.payload=dict(approved=True,authorization='Autorização de ensaio',plan_sha256='b'*64,attendance_sha256='c'*64,name_changes={},groups=[g1,g2])
    def run_import(self,payload=None):return apply(payload or self.payload,'Operador de ensaio')
    def test_import_keeps_attendance_and_certificates_independent(self):
        result=self.run_import();self.assertEqual(result['created'],3)
        t=Training.objects.get(employee=self.b);self.assertEqual(t.attendance,1);self.assertEqual(t.completed_hours,7)
        self.assertEqual(t.status,'Por confirmar');self.assertEqual(t.result,'Pendente');self.assertIsNone(t.grade)
        self.assertTrue(t.certified);self.assertIsNone(t.certificate_delivery_date);self.assertTrue(approved_undated_receipt(t))
        self.assertEqual(PrivateDocument.objects.count(),0)
    def test_reimport_does_not_duplicate_any_business_record(self):
        a=self.run_import();counts=[m.objects.count() for m in [TrainingGroup,Training,Enrollment,DocumentaryRecord,AuditEvent]]
        b=self.run_import();self.assertEqual(b['created'],0);self.assertEqual(a['training_ids'],b['training_ids'])
        self.assertEqual(counts,[m.objects.count() for m in [TrainingGroup,Training,Enrollment,DocumentaryRecord,AuditEvent]])
    def test_primary_and_secondary_preserve_existing_fields(self):
        self.run_import();self.a.refresh_from_db();self.b.refresh_from_db()
        self.assertEqual(self.a.phone,'923111111');self.assertEqual(self.b.phone,'924222222');self.assertEqual(self.b.source_data['Telefone 2'],'925333333')
    def test_no_authorisation_rejected(self):
        self.payload['approved']=False
        with self.assertRaises(ValidationError):self.run_import()
        self.assertFalse(TrainingGroup.objects.exists())
    def test_unknown_employee_never_created(self):
        self.payload['groups'][0]['rows'][0]['number']='MISSING'
        with self.assertRaises(ValidationError):self.run_import()
        self.assertEqual(Employee.objects.count(),3);self.assertFalse(TrainingGroup.objects.exists())
    def test_conflicting_contact_rolls_back_groups_and_all_changes(self):
        self.a.phone='938111111';self.a.save()
        with self.assertRaises(ValidationError):self.run_import()
        self.assertFalse(TrainingGroup.objects.exists());self.assertFalse(DocumentaryRecord.objects.exists())
    def test_other_employee_phone_cannot_be_reused(self):
        self.payload['groups'][0]['rows'][0]['contact_change']['after']=self.b.phone
        with self.assertRaises(ValidationError):self.run_import()
        self.assertFalse(TrainingGroup.objects.exists())
    def test_authorised_name_correction_only(self):
        self.payload['groups'][0]['rows'][0]['confirmed_name']='Pessoa A Corrigida'
        with self.assertRaises(ValidationError):self.run_import()
        self.payload['name_changes'][self.a.number]=dict(before='Pessoa A',after='Pessoa A Corrigida')
        self.run_import();self.a.refresh_from_db();self.assertEqual(self.a.name,'Pessoa A Corrigida')
    def test_forged_receipt_marker_does_not_bypass_existing_guards(self):
        self.run_import();t=Training.objects.get(employee=self.b)
        t.source_data['_recovery_import_v1']['authority_digest']='forged'
        with self.assertRaises(ValidationError):t.full_clean()
    def test_audit_failure_rolls_back_complete_import(self):
        with patch('training.models.AuditEvent.objects.create',side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):self.run_import()
        self.assertFalse(Training.objects.exists());self.assertFalse(TrainingGroup.objects.exists());self.a.refresh_from_db();self.assertEqual(self.a.phone,'')
    def test_six_hour_session_and_seven_hour_session(self):
        p=deepcopy(self.payload);p['groups'][1]['hours_per_day']='6';p['groups'][1]['end_time']='15:30'
        self.run_import(p);self.assertEqual(Training.objects.get(employee=self.c).completed_hours,6)
        self.assertEqual(Training.objects.get(employee=self.a).completed_hours,14)
    def test_wrong_schedule_or_duplicate_participant_rejected(self):
        p=deepcopy(self.payload);p['groups'][0]['end_time']='15:30'
        with self.assertRaises(ValidationError):self.run_import(p)
        p=deepcopy(self.payload);p['groups'][0]['rows'].append(deepcopy(p['groups'][0]['rows'][0]))
        with self.assertRaises(ValidationError):self.run_import(p)
    def test_empty_mark_not_converted_to_absence(self):
        self.payload['groups'][0]['rows'][0]['marks'][1]=None
        with self.assertRaises(ValidationError):self.run_import()
    def test_existing_group_with_other_dates_not_overwritten(self):
        self.run_import();p=deepcopy(self.payload);p['groups'][0]['dates']=['2026-01-06','2026-01-07']
        with self.assertRaises(ValidationError):self.run_import(p)
        self.assertEqual(Training.objects.count(),3)

    def handovers(self):
        from .recovery_import import confirm_handovers
        bindings={t.pk:t.source_data['_recovery_import_v1']['certificate']['sha256'] for t in Training.objects.filter(certificate_issued=True)}
        return confirm_handovers(bindings,'Todos entregues ao formando ou RH, conforme confirmação expressa.','Operador')

    def test_handover_unknown_recipient_does_not_invent_learner_hr_or_date(self):
        self.run_import();self.assertEqual(self.handovers()['updated'],2)
        a=Training.objects.get(employee=self.a);b=Training.objects.get(employee=self.b);c=Training.objects.get(employee=self.c)
        self.assertTrue(a.delivery_reported);self.assertTrue(b.delivery_reported);self.assertFalse(c.delivery_reported)
        self.assertIsNone(a.certified);self.assertIsNone(a.certificate_hr_delivered);self.assertIsNone(a.certificate_delivery_date)
        self.assertIsNone(a.certificate_hr_delivery_date);self.assertEqual(a.completed_hours,14);self.assertEqual(a.status,'Por confirmar')
        self.assertTrue(b.certified);self.assertIsNone(b.certificate_delivery_date)
        from .analytics import statistics
        stats=statistics(list(Training.objects.all()));self.assertEqual(stats['certificates_reported'],2);self.assertEqual(stats['certificates_obtained'],2)
        self.assertEqual(stats['certificates_reported_pending'],0)

    def test_handover_repetition_is_idempotent(self):
        self.run_import();self.handovers();count=AuditEvent.objects.count()
        self.assertEqual(self.handovers()['updated'],0);self.assertEqual(AuditEvent.objects.count(),count)

    def test_handover_rejects_missing_certificate_and_rolls_back_audit_failure(self):
        from .recovery_import import confirm_handovers
        self.run_import();c=Training.objects.get(employee=self.c)
        with self.assertRaises(ValidationError):confirm_handovers({c.pk:'a'*64},'Confirmado','Operador')
        before=list(Training.objects.values_list('pk','source_data'))
        with patch('training.models.AuditEvent.objects.create',side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):self.handovers()
        self.assertEqual(list(Training.objects.values_list('pk','source_data')),before)

    def test_handover_needs_valid_authority_even_with_compact_dashboard_data(self):
        self.run_import();self.handovers();a=Training.objects.get(employee=self.a)
        a._dashboard_source={'_certificate_summary':a.source_data['_certificate_summary']}
        self.assertTrue(a.delivery_reported)
        a._dashboard_source['_certificate_summary']['handover']['authority_digest']='forged'
        self.assertFalse(a.delivery_reported)

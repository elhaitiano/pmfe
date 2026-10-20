"""Synthetic transactional evaluation supplements; no official Excel/SQLite."""
import copy,hashlib,json
from datetime import date
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db import connection
from .models import Employee,Training,AuditEvent,Course,TrainingGroup
from .approved_evaluations import apply,binding,digest,ITEM,validate
from .historical_reconciliation import tablehash
from .source_achievement import attach

class ApprovedEvaluationTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='SYN-EVAL',name='Synthetic worker')
        self.rows=[Training.objects.create(employee=self.person,title='Synthetic evaluation',source_key='SYN-EVAL-'+str(i),start=date(2026,1,1),end=date(2026,1,1),days=1,hours_per_day=7,attendance=0,source_data={'original':'preserved'}) for i in range(2)]
        self.backup={'sha256':'synthetic','backup':'synthetic-memory','table_hashes':{t:tablehash(connection,t) for t in ['training_training','training_employee','training_auditevent','training_course']}}
        ops=[]
        for i,t in enumerate(self.rows):
            with connection.cursor() as c:c.execute('select source_data from training_training where id=%s',[t.pk]);raw=c.fetchone()[0]
            ops.append({'training_id':t.pk,'source_key':t.source_key,'employee_id':t.employee_id,'employee_number_sha256':hashlib.sha256(self.person.number.encode()).hexdigest(),'expected_version':1,'expected_grade':None,'signature':binding(t,raw),'unit':'percentage' if i==0 else 'qualitative','raw':'0' if i==0 else 'Bom','percentage':'0' if i==0 else None,'source_refs':['synthetic:1']})
        self.plan={'sources':[],'backup_sha256':'synthetic','operations':ops,'approval':'Synthetic confirmed authority'}
        self.sign()
    def sign(self):
        self.plan.pop('plan_hash',None);self.plan['plan_hash']=digest(self.plan)
    def execute(self,**kw):return apply(self.plan,self.backup,self.plan['plan_hash'],'synthetic-authorised',**kw)
    def test_atomic_typed_supplement_and_source_preservation(self):
        result=self.execute();self.assertEqual(result['evaluations'],2)
        self.assertEqual(AuditEvent.objects.count(),3)
        self.assertEqual(tablehash(connection,'training_training'),self.backup['table_hashes']['training_training'])
        attach(self.rows);self.assertEqual(self.rows[0].source_achievement['percentage'],0)
        self.assertTrue(self.rows[1].source_achievement['qualitative']);self.assertTrue(self.rows[1].source_achievement['approved'])
        self.assertEqual(TrainingGroup.objects.count(),0)
    def test_failure_rolls_back_every_event(self):
        with self.assertRaises(ValidationError):self.execute(fail_at=0)
        self.assertEqual(AuditEvent.objects.count(),0)
    def test_confirmation_cannot_repeat(self):
        self.execute()
        with self.assertRaises(ValidationError):self.execute()
        self.assertEqual(AuditEvent.objects.count(),3)
    def test_stale_version_blocks_before_supplement(self):
        Training.objects.filter(pk=self.rows[0].pk).update(version=2)
        with self.assertRaises(ValidationError):self.execute()
        self.assertEqual(AuditEvent.objects.count(),0)
    def test_existing_user_grade_never_overwritten(self):
        Training.objects.filter(pk=self.rows[0].pk).update(grade=15,status='Concluído')
        with self.assertRaises(ValidationError):self.execute()
        self.assertEqual(Training.objects.get(pk=self.rows[0].pk).grade,15)
        self.assertEqual(AuditEvent.objects.count(),0)
    def test_tampered_hash_rejected(self):
        self.plan['operations'][0]['percentage']='100'
        with self.assertRaises(ValidationError):self.execute()
    def test_nonfinite_and_undocumented_scales_rejected(self):
        for unit,value in [('percentage','NaN'),('percentage','101'),('percentage','-1'),('unknown','70')]:
            with self.subTest(unit=unit,value=value):
                self.plan['operations'][0]['unit']=unit;self.plan['operations'][0]['percentage']=value;self.sign()
                with self.assertRaises(ValidationError):validate(self.plan,self.plan['plan_hash'])
    def test_changed_binding_refused_even_with_current_baseline(self):
        self.plan['operations'][0]['signature']='changed';self.sign()
        with self.assertRaises(ValidationError):self.execute()
        self.assertEqual(AuditEvent.objects.count(),0)

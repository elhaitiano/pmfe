import json,tempfile,io
from pathlib import Path
from datetime import date
from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError
from .models import Training,Employee,Course,PlanningLock,AuditEvent
from .original_assessments import label,percentage
from .analytics import statistics

class OriginalAssessmentsTests(TestCase):
 def setUp(self):
  PlanningLock.objects.get_or_create(pk=1)
  self.e=Employee.objects.create(number='TESTE_NOTAS_1',name='TESTE_NOTAS_1')
  self.c=Course.objects.create(code='TESTE_NOTAS',title='TESTE_NOTAS')
  self.t=Training.objects.create(employee=self.e,course=self.c,title=self.c.title,start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=0,source_key='pilar3b:TESTE_NOTAS',grade=None,result='Pendente')
  self.a={'raw':'0.625','normalized_ratio':'0.625','kind':'percentage','source_sha256':'abc','demonstration':True}
  self.item={'training_id':self.t.pk,'source_key':self.t.source_key,'employee_number':self.e.number,'course_code':self.c.code,'start':'2025-01-01','end':'2025-01-01','expected_grade':None,'expected_result':'Pendente','assessment':self.a}
 def run_import(self,items=None,apply=True):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'manifest.json';p.write_text(json.dumps({'identification':'AMBIENTE_DE_DEMONSTRACAO','source':{'sha256':'abc'},'records':items or [self.item]}))
   out=io.StringIO();call_command('import_original_assessments',str(p),*(['--apply'] if apply else []),stdout=out)
   return json.loads(out.getvalue())
 def test_exact_percentage_label(self):
  self.assertEqual(str(percentage(self.a)),'62.500');self.assertIn('62,5%',label(self.a));self.assertIn('0.625',label(self.a))
 def test_qualitative_not_numeric(self):
  a={'raw':'Excelente','kind':'classification'};self.assertIsNone(percentage(a));self.assertEqual(label(a),'Excelente')
 def test_zero_not_missing(self):
  a={'raw':'0','normalized_ratio':'0','kind':'percentage'};self.assertEqual(percentage(a),0);self.assertIn('0%',label(a))
 def test_import_no_academic_or_certificate_inference(self):
  self.assertEqual(self.run_import()['imported'],1);self.t.refresh_from_db()
  self.assertIsNone(self.t.grade);self.assertEqual(self.t.result,'Pendente');self.assertFalse(self.t.attendance_confirmed);self.assertIsNone(self.t.certified)
  self.assertEqual(self.t.original_assessment,self.a)
 def test_idempotent_and_audited(self):
  self.run_import();self.t.refresh_from_db();version=self.t.version
  result=self.run_import();self.t.refresh_from_db();self.assertEqual(result['existing'],1);self.assertEqual(result['imported'],0);self.assertEqual(self.t.version,version);self.assertEqual(AuditEvent.objects.filter(action='IMPORT_ORIGINAL_ASSESSMENTS').count(),1)
 def test_identity_mismatch_rolls_back(self):
  item=dict(self.item,employee_number='WRONG')
  with self.assertRaises(CommandError):self.run_import([item])
  self.t.refresh_from_db();self.assertEqual(self.t.original_assessment,{})
 def test_dry_run_read_only(self):
  revision=PlanningLock.objects.get(pk=1).revision
  self.assertEqual(self.run_import(apply=False)['missing'],1)
  self.t.refresh_from_db();self.assertEqual(self.t.original_assessment,{});self.assertEqual(PlanningLock.objects.get(pk=1).revision,revision)
 def test_statistics_separates_scales(self):
  self.run_import();self.t.refresh_from_db();s=statistics([self.t]);self.assertEqual(s['original_assessments_count'],1);self.assertEqual(s['graded_count'],0);self.assertIsNone(s['average'])

 def test_history_template_shows_original_scale(self):
  from django.template.loader import render_to_string
  self.run_import();self.t.refresh_from_db()
  html=render_to_string('timeline.html',{'trainings':[self.t]})
  self.assertIn(self.t.original_assessment_label,html)

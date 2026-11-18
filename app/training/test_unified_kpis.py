from datetime import date,time
from decimal import Decimal
from io import BytesIO
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from openpyxl import load_workbook
from .models import Course,Employee,Training,TrainingGroup
from .analytics import statistics,attendance_statistics
from .operational_kpis import attendance_summary,certification_summary
from .man_days import metrics
from .scheduling import save_group
from . import attendance_import
from .attendance_template import make_workbook

@override_settings(PMF_VALIDATION_ENABLED=False,PMF_HISTORICAL_ENABLED=False,PMF_ATTENDANCE_IMPORT_ENABLED=True)
class UnifiedKpiTests(TestCase):
 def setUp(self):
  self.person=Employee.objects.create(number='TEST-KPI-1',name='Pessoa ficticia')
  self.course=Course.objects.create(title='Ensaio KPI',area='Técnica')
 def row(self,hours=7,present=1,obtained=None,day=1,group=None):
  marks=['P' if present else 'A'];d=date(2026,1,day)
  return Training.objects.create(employee=self.person,course=self.course,group=group,title=self.course.title,area='Técnica',start=d,end=d,days=1,hours_per_day=hours,attendance=present,attendance_confirmed=True,status='Concluído',source_data={'_attendance_import_v1':{'source_sha256':'synthetic-hash','hours':str(hours),'dates':[d.isoformat()],'row':{'employee':self.person.pk,'marks':marks,'certificate':obtained,'reference':'REF' if obtained else ''}}})
 def test_six_and_seven_hours_sum_and_hd(self):
  a,b=self.row(6,day=1),self.row(7,day=2);s=statistics([a,b]);self.assertEqual(s['hours'],13);self.assertEqual(s['attendance'],100);m=metrics([a,b]);self.assertEqual(m['hours'],13);self.assertEqual(m['equivalent'],Decimal('1.86'));self.assertEqual(m['effective_days'],2)
 def test_attendance_is_weighted_by_hours(self):
  a,b=self.row(6,1),self.row(7,0,day=2);s=attendance_statistics([a,b]);self.assertEqual(s['attendance'],Decimal('46.15'));self.assertEqual(s['attendance_present_hours'],6);self.assertEqual(s['attendance_planned_hours'],13)
 def test_unknown_attendance_is_not_zero(self):
  a=self.row();a.attendance_confirmed=False;a.save();s=statistics([a]);self.assertIsNone(s['attendance']);self.assertEqual(s['verified_hours_records'],0)
 def test_confirmed_zero_is_zero(self):
  s=statistics([self.row(present=0)]);self.assertEqual(s['attendance'],0);self.assertEqual(s['hours'],0);self.assertEqual(s['verified_hours_records'],1)
 def test_repeated_input_does_not_double_hours(self):
  a=self.row();self.assertEqual(statistics([a,a])['hours'],7);self.assertEqual(metrics([a,a])['hours'],7)
 def test_same_day_distinct_formations_has_one_effective_day(self):
  a,b=self.row(6),self.row(7);m=metrics([a,b]);self.assertEqual(m['hours'],13);self.assertEqual(m['effective_days'],1)
 def test_obtained_does_not_infer_issue_or_delivery(self):
  a=self.row(obtained=True);s=statistics([a]);self.assertEqual(s['certificates_obtained'],1);self.assertEqual(s['certificates_issued'],0);self.assertEqual(s['certificates_reported'],0);self.assertEqual(s['certification']['issue_rate']['value'],0)
 def test_certificate_unknown_is_distinct_from_no(self):
  a=self.row(obtained=None);self.assertEqual(statistics([a])['certificate_obtained_known'],0);b=self.row(obtained=False,day=2);s=statistics([a,b]);self.assertEqual(s['certificate_obtained_known'],1);self.assertEqual(s['certificates_obtained'],0)
 def test_legacy_delivery_is_preserved(self):
  a=self.row();a.certified=True;a.save();self.assertEqual(statistics([a])['certificates_reported'],1);self.assertEqual(statistics([a])['certificates_obtained'],0);a.refresh_from_db();self.assertTrue(a.certified)
 def test_import_repeat_and_independent_certificate_states(self):
  user=get_user_model().objects.create_superuser('kpi-import','test@example.invalid','test-only')
  g=save_group(self.course.pk,dict(dates=[date(2026,1,1)],start=date(2026,1,1),end=date(2026,1,1),trainer='Formador de ensaio',provider='EPAL',location='Local de ensaio',capacity=2,hours_per_day=Decimal(6),break_hours=Decimal(1),start_time=time(8),end_time=time(16)),actor=user.username)
  content=make_workbook(g,people=[self.person]);wb=load_workbook(BytesIO(content));ws=wb['Assiduidade'];ws['C5']='P';ws['D5']='Sim';ws['E5']='REF-TEST';out=BytesIO();wb.save(out);payload=attendance_import.parse_file(out.getvalue(),g);proposal=attendance_import.preview(user,g,payload);first=attendance_import.apply(user,g.pk,payload,proposal['token']);again=attendance_import.apply(user,g.pk,payload,proposal['token']);self.assertEqual(first['created'],1);self.assertEqual(again['created'],0)
  row=Training.objects.get(group=g);self.assertIsNone(row.certificate_issued);self.assertIsNone(row.certified);s=statistics([row]);self.assertEqual(s['certificates_obtained'],1);self.assertEqual(s['certificates_reported'],0);self.assertEqual(metrics([row])['hours'],6)
 def test_same_totals_in_dashboard_profile_print_excel_and_pdf(self):
  from django.urls import reverse
  from .executive_views import context
  from .man_days import workbook
  from .executive_pdf import render_report
  from django.test import RequestFactory
  self.row(6,obtained=True,day=1);self.row(7,obtained=False,day=2)
  user=get_user_model().objects.create_superuser('kpi-report','test@example.invalid','test-only');self.client.force_login(user)
  for name,args in [('dashboard',[]),('employee_data',[self.person.pk]),('employee_print',[self.person.pk]),('executive_reports',[])]:
   response=self.client.get(reverse(name,args=args));self.assertEqual(response.status_code,200);self.assertEqual(response.context['stats']['hours'],13);self.assertEqual(response.context['stats']['certificates_obtained'],1);self.assertEqual(response.context['stats']['certificates_reported'],0)
  request=RequestFactory().get('/dashboard/',{'period':''});request.user=user;data,valid=context(request);self.assertTrue(valid);self.assertEqual(data['stats']['hours'],13);self.assertEqual(data['man_days']['equivalent'],Decimal('1.86'))
  excel=workbook(data);wb=load_workbook(BytesIO(excel.content),data_only=True);summary={r[0]:r[1] for r in wb['Resumo'].iter_rows(values_only=True)};self.assertEqual(summary['Homens-Dia de Formação (H/D)'],1.86);self.assertEqual(summary['Certificados obtidos'],1);self.assertEqual(summary['Certificados entregues'],0)
  pdf=render_report(data,'TEST-KPI','ensaio');self.assertTrue(pdf.startswith(b'%PDF'))

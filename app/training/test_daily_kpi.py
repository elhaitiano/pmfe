from datetime import date
from decimal import Decimal
from io import BytesIO
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from pypdf import PdfReader
from .models import Employee, Training, GroupSession, SessionAttendance, Enrollment
from .test_sessions import group_fixture, training_fixture
from .analytics import statistics
from .kpi_service import Scope

class DailyKpiTests(TestCase):
    def setUp(self):
        self.group=group_fixture()
        self.a=training_fixture(self.group,'A')
        self.b=training_fixture(self.group,'B')
        self.jan=GroupSession.objects.create(group=self.group,date=date(2025,1,31),duration=6)
        self.feb=GroupSession.objects.create(group=self.group,date=date(2025,2,1),duration=7)
        for row,jan,feb in ((self.a,6,Decimal('3.5')),(self.b,0,0)):
            for session,hours in ((self.jan,jan),(self.feb,feb)):
                SessionAttendance.objects.create(session=session,training=row,hours=hours,confirmed=True,actor='RH')
        self.user=get_user_model().objects.create_superuser('daily-reviewer',password='test')
        self.client.force_login(self.user)
    def data(self,month=None):
        params={'year':'2025','period':'year'}
        if month:params.update(period='month',month=str(month))
        response=self.client.get(reverse('dashboard'),params)
        self.assertEqual(response.status_code,200)
        return response.context
    def test_real_months_weighting_and_annual_distincts(self):
        january=self.data(1)['kpi_summary'];february=self.data(2)['kpi_summary'];annual=self.data()['kpi_summary']
        self.assertEqual(january.metrics['hours'].value,6)
        self.assertEqual(february.metrics['hours'].value,Decimal('3.5'))
        self.assertEqual(january.metrics['attendance'].value,50)
        self.assertEqual(february.metrics['attendance'].value,25)
        self.assertEqual(annual.metrics['attendance'].value,Decimal('36.54'))
        self.assertEqual(annual.metrics['people'].value,1)
        self.assertEqual(annual.metrics['equivalent_days'].value,Decimal('1.36'))
        self.assertEqual(annual.effective_days,2)
    def test_same_person_same_day_in_two_editions_preserves_hours(self):
        group=group_fixture('Second edition')
        row=Training.objects.create(employee=self.a.employee,group=group,course=group.course,title=group.title,
            start=group.start,end=group.end,days=2,hours_per_day=7,attendance=0,status='Concluído')
        session=GroupSession.objects.create(group=group,date=self.jan.date,duration=6)
        SessionAttendance.objects.create(session=session,training=row,hours=6,confirmed=True,actor='RH')
        summary=self.data()['kpi_summary']
        self.assertEqual(summary.metrics['hours'].value,Decimal('15.5'))
        self.assertEqual(summary.metrics['participations'].value,2)
        self.assertEqual(summary.metrics['people'].value,1)
        self.assertEqual(summary.effective_days,2)
    def test_legacy_not_distributed_and_global_totals_do_not_double_count(self):
        legacy=Training.objects.create(employee=self.a.employee,title='Legacy',start=date(2025,1,30),end=date(2025,2,2),
            days=2,hours_per_day=7,attendance=2,attendance_confirmed=True)
        self.a.attendance=2;self.a.attendance_confirmed=True;self.a.save()
        monthly=self.data(1)['kpi_summary']
        self.assertEqual(monthly.metrics['hours'].value,6)
        self.assertEqual(monthly.unallocated_participations,1)
        self.assertIsNone(monthly.effective_days)
        self.assertIn('legacy_without_daily_dates',monthly.metrics['hours'].reasons)
        global_stats=statistics(Training.objects.select_related('group'),Employee.objects.all())
        self.assertEqual(global_stats['hours'],Decimal('23.5'))
    def test_partial_evidence_withholds_complete_days(self):
        SessionAttendance.objects.filter(training=self.b,session=self.feb).delete()
        summary=self.data(2)['kpi_summary']
        self.assertEqual(summary.metrics['attendance'].value,50)
        self.assertEqual(summary.metrics['hours'].status,'partial')
        self.assertIsNone(summary.effective_days)
    def test_history_profile_and_exports_use_same_daily_hours(self):
        history=self.client.get(reverse('employee_detail',args=[self.a.employee_id]),{'year':'2025'}).context['stats']
        self.assertEqual(history['hours'],Decimal('9.5'))
        params={'year':'2025','period':'month','month':'2'}
        report=self.client.get(reverse('participation_executive'),params).context['report']
        self.assertEqual(report['kpi_summary'].metrics,self.data(2)['kpi_summary'].metrics)
        response=self.client.get(reverse('participation_executive'),{**params,'export':'xlsx'})
        book=load_workbook(BytesIO(response.content))
        self.assertEqual(book.sheetnames,['Protecção'])
        response=self.client.get(reverse('executive_preview'),params)
        text=' '.join(p.extract_text() for p in PdfReader(BytesIO(b''.join(response.streaming_content))).pages)
        self.assertIn('<5',text)
    def test_month_drill_uses_session_date(self):
        response=self.client.get(reverse('executive_explore'),{'year':'2025','slice':'time','value':'2025-02'})
        self.assertEqual(response.context['detail_stats']['hours'],Decimal('3.5'))
    def test_ytd_and_custom_interval(self):
        params={'period':'ytd','year':'2025'}
        self.assertEqual(self.client.get(reverse('dashboard'),params).context['stats']['hours'],Decimal('9.5'))
        params={'period':'custom','date_from':'2025-02-01','date_to':'2025-02-01'}
        self.assertEqual(self.client.get(reverse('dashboard'),params).context['stats']['hours'],Decimal('3.5'))

    def test_missing_legacy_duration_does_not_break_daily_indicators(self):
        Training.objects.filter(pk=self.a.pk).update(days=None,hours_per_day=None)
        response=self.client.get(reverse('indicator_index'),{'start':'2025-01-01','end':'2025-12-31'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['attendance'],Decimal('36.54'))

    def test_unknown_session_of_confirmed_participant_withholds_average(self):
        SessionAttendance.objects.filter(training=self.a,session=self.feb).delete()
        summary=self.data()['kpi_summary']
        self.assertEqual(summary.metrics['hours'].value,6)
        self.assertIsNone(summary.metrics['hours_per_person'].value)

    def test_finished_group_accepts_verified_daily_records_without_rewriting_legacy(self):
        for row in (self.a,self.b):
            Enrollment.objects.create(group=self.group,employee=row.employee,training=row)
        self.group.state='Em Curso';self.group.save()
        response=self.client.post(reverse('group_transition',args=[self.group.pk]),{'revision':self.group.revision,'action':'finish'})
        self.assertEqual(response.status_code,302)
        self.group.refresh_from_db();self.a.refresh_from_db()
        self.assertEqual(self.group.state,'Concluída')
        self.assertFalse(self.a.attendance_confirmed)

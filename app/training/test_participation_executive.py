from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError,transaction
from django.urls import reverse
from django.core.exceptions import ValidationError
from openpyxl import load_workbook
from .models import Employee,Course,ConsolidatedEdition,ConsolidatedParticipation,Training,AuditEvent
from .participation_executive import analyse,Filters,completion
from .completion_presentation import summary

class ParticipationExecutiveTests(TestCase):
    def setUp(self):
        self.a=Employee.objects.create(number='EX1',name='Pessoa Alfa',direction='Norte')
        self.b=Employee.objects.create(number='EX2',name='Pessoa Beta',direction='Sul')
        self.c=Employee.objects.create(number='EX3',name='Pessoa Gama',direction='Norte')
        self.course=Course.objects.create(title='Segurança operacional',code='SEG1')
        self.other=Course.objects.create(title='Segurança operacional complementar',code='SEG2')
        self.ed=ConsolidatedEdition.objects.create(source_key='exec-ed1',course=self.course,title=self.course.title,course_reference='SEG1',group_label='G1',start=date(2025,1,1),end=date(2025,1,2))
        self.p=ConsolidatedParticipation.objects.create(source_key='exec-p1',employee=self.a,edition=self.ed,present_count=2,absent_count=0,attendance_complete=True,attendance_percent=100,attended_hours=14,completed=True)
        ConsolidatedParticipation.objects.create(source_key='exec-p2',employee=self.b,edition=self.ed,present_count=None,attendance_complete=False)
        self.user=get_user_model().objects.create_user('executive-reader')
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=['view_employee','view_training']))
    def data(self,**kw):return analyse({'course':self.course,**kw})
    def test_unique_people_across_legitimate_editions(self):
        e=ConsolidatedEdition.objects.create(source_key='exec-ed2',course=self.course,title=self.course.title,group_label='G2',start=date(2026,1,1),end=date(2026,1,1))
        ConsolidatedParticipation.objects.create(source_key='exec-p3',employee=self.a,edition=e,present_count=1,absent_count=0,attendance_complete=True,attended_hours=7,completed=True)
        d=self.data();self.assertEqual((d['universe'],d['participated'],d['not_participated'],d['completed']),(3,1,2,1));self.assertEqual(len(d['nominal'][0]['editions']),2);self.assertEqual(d['rate'],33.3)
    def test_registered_is_not_participated(self):
        d=self.data(state='registered');self.assertEqual([r['number'] for r in d['nominal']],['EX2'])
    def test_without_training_is_not_claim_of_no_external_training(self):
        d=self.data(state='no_training');self.assertEqual({r['number'] for r in d['nominal']},{'EX2','EX3'})
    def test_direction_denominator_and_multiple_directions(self):
        d=self.data(directions=['Norte']);self.assertEqual((d['universe'],d['participated'],d['rate']),(2,1,50));self.assertEqual(self.data(directions=['Norte','Sul'])['universe'],3)
    def test_course_identity_not_similar_titles(self):
        self.assertEqual(analyse({'course':self.other})['participated'],0)
    def test_period_filters_do_not_remove_universe(self):
        d=self.data(year=2026);self.assertEqual((d['universe'],d['participated']),(3,0));self.assertEqual(self.data(start=date(2025,1,2),end=date(2025,1,3))['participated'],1)
    def test_group_and_completion_filters(self):
        d=self.data(group='c:'+str(self.ed.pk),completion='completed');self.assertEqual([r['number'] for r in d['nominal']],['EX1'])
    def test_search_restricts_list_not_coverage_denominator(self):
        d=self.data(q='EX2');self.assertEqual(d['universe'],3);self.assertEqual([r['number'] for r in d['nominal']],['EX2'])
    def test_zero_denominator_is_not_zero_rate(self):
        d=self.data(directions=['Sem técnicos']);self.assertIsNone(d['rate'])
    def test_inconsistent_zero_presence_cannot_confirm_completion(self):
        self.p.present_count=0;self.assertEqual(completion(self.p),'pending')
    def test_filters_reject_invalid_dates_and_backwards_interval(self):
        for data in [{'start':'31/02/2026'},{'start':'02/03/2026','end':'01/03/2026'},{'group':'c:999999'}]:self.assertFalse(Filters(data).is_valid())
        self.assertTrue(Filters({'start':'01/03/2026','end':'02/03/2026'}).is_valid())
    def test_anonymous_and_unpermitted_requests_cannot_export(self):
        url=reverse('participation_executive');self.assertEqual(self.client.get(url).status_code,302)
        user=get_user_model().objects.create_user('unpermitted',is_staff=True);self.client.force_login(user)
        for suffix in ['', '?export=xlsx','?export=pdf']:self.assertEqual(self.client.get(url+suffix).status_code,403)
    def test_reader_can_consult_and_post_is_rejected(self):
        self.client.force_login(self.user);url=reverse('participation_executive')
        self.assertContains(self.client.get(url),'Participação por Direcção');self.assertEqual(self.client.post(url,{}).status_code,405)
    def test_excel_totals_match_screen_and_formula_safe(self):
        self.a.name='=1+1';self.a.save(update_fields=['name']);self.client.force_login(self.user)
        r=self.client.get(reverse('participation_executive'),{'course':self.course.pk,'export':'xlsx'})
        w=load_workbook(BytesIO(r.content),data_only=False);self.assertEqual(w['Lista nominal'].max_row,4)
        self.assertEqual(w['Lista nominal']['B2'].value,"'=1+1");self.assertEqual(dict(list(w['Resumo'].values)[1:])['Universo'],3)
    def test_pdf_integrity_and_no_write(self):
        self.client.force_login(self.user);before=list(ConsolidatedParticipation.objects.values());events=AuditEvent.objects.count()
        response=self.client.get(reverse('participation_executive'),{'course':self.course.pk,'export':'pdf'});self.assertTrue(response.content.startswith(b'%PDF-'));self.assertIn(b'%%EOF',response.content[-100:]);self.assertEqual(before,list(ConsolidatedParticipation.objects.values()));self.assertEqual(events,AuditEvent.objects.count())
    def test_duplicate_import_unique_constraint(self):
        with self.assertRaises(IntegrityError),transaction.atomic():ConsolidatedParticipation.objects.bulk_create([ConsolidatedParticipation(source_key='different-source',employee=self.a,edition=self.ed)])
    def test_exact_course_history_requires_authorisation_not_title_guess(self):
        from .eligibility import assess
        Training.objects.create(employee=self.a,course=self.course,title='Título histórico diferente',start=date(2025,1,1),end=date(2025,1,1),attendance=1,attendance_confirmed=True,status='Concluído')
        with patch('training.historical.enabled',return_value=False):
            item=assess(self.course,[self.a])[0];other=assess(self.other,[self.a])[0]
        self.assertTrue(item['proven']);self.assertEqual(item['decision_kind'],'repeat');self.assertFalse(other['proven'])
    def test_certificate_expected_without_pdf_only_with_explicit_eligibility(self):
        t=Training.objects.create(employee=self.a,course=self.course,title='Sem PDF',start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1,attendance_confirmed=True,status='Concluído',source_data={'certificate_eligible':True})
        result=summary(self.a.pk);self.assertEqual(result['totals']['expected'],1);self.assertEqual(result['totals']['delivered'],0);self.assertIsNone(Training.objects.get(pk=t.pk).certified)
    def test_no_grade_or_eligibility_is_invented(self):
        t=Training.objects.create(employee=self.a,course=self.course,title='Sem regra',start=date(2025,1,1),end=date(2025,1,1),attendance=1,attendance_confirmed=True,status='Concluído')
        result=summary(self.a.pk);self.assertEqual(result['totals']['expected'],0);self.assertEqual(result['totals']['verify'],1);self.assertIsNone(Training.objects.get(pk=t.pk).grade)
    def test_minimum_grade_when_explicitly_required(self):
        self.course.source_metadata={'certificate_rules':{'minimum_grade':10}};self.course.save()
        Training.objects.create(employee=self.a,course=self.course,title='Nota obrigatória ausente',start=date(2025,1,1),end=date(2025,1,1),attendance=1,attendance_confirmed=True,status='Concluído',source_data={'certificate_eligible':True})
        self.assertEqual(summary(self.a.pk)['totals']['expected'],0)
    def test_positive_canonical_presence_with_incomplete_denominator(self):
        self.p.attendance_complete=False;self.p.attendance_percent=None;self.p.save()
        d=self.data();self.assertEqual(d['participated'],1);self.assertEqual(d['completed'],0)
    def test_catalogue_code_search_does_not_match_other_courses(self):
        self.assertEqual(analyse({'catalogue_search':'SEG1'})['participated'],1)
        self.assertEqual(analyse({'catalogue_search':'SEG2'})['participated'],0)
    def test_missing_period_does_not_crash_and_is_not_dated_by_inference(self):
        self.ed.start=None;self.ed.end=None;self.ed.save()
        self.assertTrue(Filters({}).is_valid());self.assertEqual(self.data(year=2025)['participated'],0)
    def test_nullable_attendance_legacy_enrolment_review(self):
        from .planning import _legacy_history_rows
        from .scheduling import save_group
        from datetime import time
        Training.objects.create(employee=self.a,course=self.course,title=self.course.title,start=date(2025,1,1),end=date(2025,1,1),attendance=None,attendance_confirmed=True)
        # No database writes in the review, no guessed zero or completion.
        self.course.validation_status='validated';self.course.validation_notes='Referência sintética';self.course.save()
        group=save_group(self.course.pk,dict(start=date(2027,1,1),end=date(2027,1,1),dates=[date(2027,1,1)],trainer='Formador sintético',location='Sala sintética',capacity=20,hours_per_day=7,break_hours=1,start_time=time(8),end_time=time(16),planning_confirmation='Provisorio'))
        entry=_legacy_history_rows(group,[self.a])[0];self.assertTrue(entry['blocked']);self.assertEqual(entry['warnings'][0]['kind'],'Participação por confirmar')
    def test_query_count_does_not_grow_per_employee(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        for i in range(25):Employee.objects.create(number='PERF'+str(i),name='Pessoa sintética '+str(i),direction='Norte')
        with CaptureQueriesContext(connection) as queries:d=self.data()
        self.assertEqual(d['universe'],28);self.assertLessEqual(len(queries),10)  # bounded shared reconciliation, never per employee
    def test_identity_policy_is_respected_in_consultation_and_exports(self):
        from types import SimpleNamespace
        self.client.force_login(self.user)
        policy=SimpleNamespace(ok=lambda kind,pk,dimension:pk!=self.a.pk)
        with patch('training.data_validation.enabled',return_value=True),patch('training.data_validation.can_review',return_value=False),patch('training.data_validation.policy',return_value=policy):
            d=analyse({'course':self.course},user=self.user)
            self.assertEqual(d['universe'],2);self.assertNotIn(self.a.pk,[r['employee'].pk for r in d['nominal']])

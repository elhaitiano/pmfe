from copy import deepcopy
from datetime import date,time
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from django.test import TestCase,override_settings,RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError,PermissionDenied
from django.urls import reverse
from pypdf import PdfReader
from openpyxl import load_workbook
from .models import Course,Employee,Enrollment,Training,FormationQuestionnaire,FormationSurvey,FormationSurveyResponse,FormationSurveyAggregate,AuditEvent
from .scheduling import save_group
from .formation_questionnaire import SCHEMA,digest,question_metrics
from . import formation_survey_service as service
from .formation_survey_pdf import form_pdf,source
from .formation_survey_exports import export

@override_settings(PMF_VALIDATION_ENABLED=False,PMF_HISTORICAL_ENABLED=False,PMF_SIMPLIFIED_PLANNING_ENABLED=True)
class FormationSurveyTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('qa-survey','qa@example.invalid','test-only')
        self.client.force_login(self.user);self.course=Course.objects.create(title='Formação fictícia',area='Segurança')
        def group(day):return save_group(self.course.pk,dict(dates=[date(2026,1,day)],start=date(2026,1,day),end=date(2026,1,day),trainer='Formador fictício',provider='EPAL',location='Local fictício',capacity=20,hours_per_day=7,break_hours=1,start_time=time(8,30),end_time=time(16,30)),actor='qa')
        self.g1=group(2);self.g2=group(3)
        self.a=Employee.objects.create(number='QA-FS1',name='Pessoa fictícia A',direction='Unidade fictícia',phone='923000001',email='ensaio@example.invalid')
        self.b=Employee.objects.create(number='QA-FS2',name='Pessoa fictícia B')
        self.c=Employee.objects.create(number='QA-FS3',name='Pessoa externa ao grupo')
        for g,e in [(self.g1,self.a),(self.g1,self.b),(self.g2,self.a)]:Enrollment.objects.create(group=g,employee=e)
        self.version=FormationQuestionnaire.objects.get(code='proagua-formation-v1')
    def survey(self,mode='individual',anonymous=True,group=None):return service.create_survey(self.user,group or self.g1,self.version,mode,anonymous,date(2026,1,4))
    def values(self,reference='F1',answers=None,**kw):return dict(reference=reference,answers=answers or {'q01':5},improvements='',future_training='',state='completed',**kw)
    def add(self,survey,**kw):return service.save_entry(self.user,survey.pk,self.values(**kw))
    def aggregate(self,survey,counts=None,received=1,**kw):
        values=dict(counts=counts or {'q01':[0,0,0,0,1]},received_forms=received,improvements='',future_training='',state='completed',reason='Conferência de ensaio')
        values.update(kw);return service.save_entry(self.user,survey.pk,values)
    def test_official_schema_order_scale_and_binary(self):
        self.assertEqual(len(SCHEMA['questions']),15);self.assertEqual([sum(q['section']==s['key'] for q in SCHEMA['questions']) for s in SCHEMA['sections']],[4,5,3,3])
        self.assertEqual(SCHEMA['questions'][3]['kind'],'yesno');self.assertEqual(SCHEMA['scale'],['Insuficiente','Suficiente','Bom','Muito Bom','Excelente'])
    def test_existing_group_reuses_metadata_without_new_participants(self):
        n=Employee.objects.count();s=self.survey();self.assertEqual(s.group_id,self.g1.pk);self.assertEqual(Employee.objects.count(),n)
    def test_named_response_requires_actual_group_member(self):
        s=self.survey(anonymous=False);row=self.add(s,employee_id=self.a.pk);self.assertEqual(row.employee_id,self.a.pk)
        with self.assertRaises(ValidationError):self.add(s,reference='F2',employee_id=self.c.pk)
    def test_anonymous_never_saves_nominal_link(self):
        s=self.survey();self.assertIsNone(self.add(s).employee_id)
        with self.assertRaises(ValidationError):self.add(s,reference='F2',employee_id=self.a.pk)
    def test_anonymous_audit_has_no_respondent_or_free_text(self):
        s=self.survey();values=self.values();values['improvements']='ensaio@example.invalid'
        service.save_entry(self.user,s.pk,values);event=AuditEvent.objects.filter(action='FORMATION_SURVEY_ENTRY_SAVED').last()
        self.assertNotIn('employee',event.detail);self.assertNotIn(self.a.name,event.detail);self.assertNotIn('ensaio@example.invalid',event.detail)
    def test_draft_excluded_and_can_be_completed_later(self):
        s=self.survey();values=self.values();values['state']='draft';row=service.save_entry(self.user,s.pk,values)
        self.assertEqual(service.summary([s])['received'],0)
        values['state']='completed';service.save_entry(self.user,s.pk,values,pk=row.pk,revision=row.revision,reason='Concluir ficha parcial')
        self.assertEqual(service.summary([s])['received'],1)
    def test_partial_save_requires_dedicated_permission(self):
        s=self.survey();user=get_user_model().objects.create_user('partial',is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=['view_training','view_traininggroup','add_formationsurveyresponse']))
        values=self.values();values['state']='draft'
        with self.assertRaises(PermissionDenied):service.save_entry(user,s.pk,values)
    def test_missing_answer_is_not_zero(self):
        s=self.survey();self.add(s,answers={'q01':5,'q02':None});summary=service.summary([s]);self.assertEqual(summary['questions'][0]['average'],5);self.assertIsNone(summary['questions'][1]['average']);self.assertEqual(summary['valid_responses'],1)
    def test_dimension_uses_valid_answers_instead_of_mean_of_question_means(self):
        s=self.survey();self.add(s,answers={'q01':1,'q02':5});self.add(s,reference='F2',answers={'q01':1})
        self.assertEqual(service.summary([s])['dimensions'][0]['average'],Decimal(7)/3)
    def test_groups_with_different_response_numbers_are_weighted(self):
        a=self.survey();b=self.survey(group=self.g2);self.add(a,answers={'q01':1})
        for i in range(3):self.add(b,reference=f'B{i}',answers={'q01':5})
        s=service.summary([a,b]);self.assertEqual(s['questions'][0]['average'],4);self.assertEqual(s['received'],4)
    def test_binary_recommendation_and_missing_response(self):
        s=self.survey();self.add(s,answers={'q04':5});self.add(s,reference='F2',answers={'q04':None})
        summary=service.summary([s]);self.assertEqual(summary['recommendation']['yes_percent'],100);self.assertIsNone(summary['dimensions'][0]['average'])
    def test_binary_rejects_intermediate_and_zero(self):
        s=self.survey()
        for value in [0,2,3,4,True]:
            with self.subTest(value=value),self.assertRaises(ValidationError):self.add(s,answers={'q04':value})
    def test_reference_normalisation_prevents_duplicate_paper_form(self):
        s=self.survey();self.add(s,reference=' f1 ')
        with self.assertRaises(ValidationError):self.add(s,reference='F1')
    def test_identified_employee_cannot_have_second_form(self):
        s=self.survey(anonymous=False);self.add(s,employee_id=self.a.pk)
        with self.assertRaises(ValidationError):self.add(s,reference='F2',employee_id=self.a.pk)
    def test_same_anonymous_answers_can_belong_to_different_forms(self):
        s=self.survey();self.add(s);self.add(s,reference='F2');self.assertEqual(service.summary([s])['received'],2)
    def test_one_mode_per_group_avoids_adding_aggregate_and_individual(self):
        s=self.survey();self.add(s)
        with self.assertRaises(ValidationError):self.survey(mode='aggregate')
        with self.assertRaises(ValidationError):self.aggregate(s)
    def test_aggregate_counts_have_correct_denominator(self):
        s=self.survey('aggregate');self.aggregate(s,counts={'q01':[0,0,0,0,1],'q02':[0,0,0,0,0]},received=2)
        summary=service.summary([s]);self.assertEqual(summary['questions'][0]['average'],5);self.assertEqual(summary['questions'][0]['n'],1);self.assertEqual(summary['received'],2)
    def test_aggregate_received_can_exceed_roster_only_with_reason(self):
        s=self.survey('aggregate')
        with self.assertRaises(ValidationError):self.aggregate(s,received=3,reason='')
        row=self.aggregate(s,received=3,reason='Uma ficha externa, apenas ensaio');self.assertEqual(row.received_forms,3);self.assertEqual(Employee.objects.count(),3)
    def test_negative_float_and_excess_counts_rejected(self):
        s=self.survey('aggregate')
        for counts in [[-1,0,0,0,1],[0,0,0,0,2],[0,0,0,0,1.0]]:
            with self.subTest(counts=counts),self.assertRaises(ValidationError):self.aggregate(s,counts={'q01':counts})
    def test_binary_aggregate_rejects_middle_categories(self):
        s=self.survey('aggregate')
        with self.assertRaises(ValidationError):self.aggregate(s,counts={'q04':[0,1,0,0,0]})
    def test_incomplete_aggregate_line_can_be_draft_but_not_complete(self):
        s=self.survey('aggregate');counts={'q01':[None,0,0,0,1]}
        with self.assertRaises(ValidationError):self.aggregate(s,counts=counts)
        self.aggregate(s,counts=counts,state='draft');self.assertEqual(service.summary([s])['received'],0)
    def test_questionnaire_version_cannot_be_rewritten_after_use(self):
        self.survey();self.version.schema=deepcopy(self.version.schema);self.version.schema['questions'][0]['text']='Alterado';self.version.schema_digest=digest(self.version.schema)
        with self.assertRaises(ValidationError):self.version.save()
    def test_silent_version_drift_is_detected_by_results(self):
        s=self.survey();FormationQuestionnaire.objects.filter(pk=self.version.pk).update(schema_digest='a'*64)
        with self.assertRaises(ValidationError):service.summary([FormationSurvey.objects.select_related('questionnaire').get(pk=s.pk)])
    def test_stale_revision_and_missing_reason_rejected(self):
        s=self.survey();row=self.add(s)
        with self.assertRaises(ValidationError):service.save_entry(self.user,s.pk,self.values(),pk=row.pk,revision=0,reason='Ensaio')
        with self.assertRaises(ValidationError):service.save_entry(self.user,s.pk,self.values(),pk=row.pk,revision=1)
    def test_audit_failure_rolls_back_response(self):
        s=self.survey()
        with patch('training.models.AuditEvent.objects.create',side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):self.add(s)
        self.assertFalse(s.responses.exists())
    def test_mode_and_anonymity_cannot_change_after_record(self):
        s=self.survey();self.add(s);s.anonymous=False
        with self.assertRaises(ValidationError):s.clean()
    def test_pdf_blank_is_exact_official_source_and_two_pages(self):
        raw=form_pdf(self.user);self.assertEqual(raw,source().read_bytes());self.assertEqual(len(PdfReader(BytesIO(raw)).pages),2)
    def test_prefilled_group_has_two_pages_and_no_personal_data(self):
        self.survey();raw=form_pdf(self.user,'group',self.g1);reader=PdfReader(BytesIO(raw));self.assertEqual(len(reader.pages),2)
        text=''.join(p.extract_text() for p in reader.pages);self.assertIn(self.course.title,text);self.assertIn('G1',text);self.assertNotIn(self.a.name,text)
    def test_prefilled_named_print_does_not_mark_any_answers(self):
        self.survey(anonymous=False);raw=form_pdf(self.user,'person',self.g1,self.a.pk,True,True);reader=PdfReader(BytesIO(raw));text=reader.pages[0].extract_text()
        self.assertIn(self.a.name,text);self.assertIn(self.a.email,text);self.assertEqual(len(reader.pages),2)
        self.assertIn('1/2',text);self.assertIn('2/2',reader.pages[1].extract_text())
    def test_batch_print_has_two_pages_per_person(self):
        self.survey(anonymous=False);raw=form_pdf(self.user,'batch',self.g1,personal=True);self.assertEqual(len(PdfReader(BytesIO(raw)).pages),4)
    def test_anonymous_group_forbids_personal_printing(self):
        self.survey()
        with self.assertRaises(ValidationError):form_pdf(self.user,'person',self.g1,self.a.pk,True)
    def test_print_permission_and_personal_permission_are_separate(self):
        user=get_user_model().objects.create_user('printer',is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=['view_training','view_traininggroup','print_formation_evaluation']))
        self.survey(anonymous=False);self.assertTrue(form_pdf(user).startswith(b'%PDF'))
        with self.assertRaises(PermissionDenied):form_pdf(user,'person',self.g1,self.a.pk,True)
    def test_excel_and_pdf_use_same_summary_and_safe_comments(self):
        s=self.survey();values=self.values();values['improvements']='=HYPERLINK("bad") ensaio@example.invalid';service.save_entry(self.user,s.pk,values)
        data=service.summary([s]);xlsx=export(data,'xlsx');book=load_workbook(BytesIO(xlsx.content),data_only=False)
        self.assertEqual(book['Resumo']['B3'].value,1);self.assertTrue(book['Perguntas']['J2'].value.startswith('=IF'))
        self.assertTrue(book['Sugestões']['B2'].value.startswith("'="));self.assertNotIn('ensaio@example.invalid',book['Sugestões']['B2'].value)
        pdf=export(data,'pdf');self.assertTrue(pdf.content.startswith(b'%PDF'));self.assertGreaterEqual(len(PdfReader(BytesIO(pdf.content)).pages),1)
    def test_nominal_filter_never_assigns_anonymous_group_to_employee(self):
        s=self.survey();self.add(s);result=service.summary([s],{self.a.pk});self.assertEqual(result['received'],0);self.assertEqual(result['excluded_anonymous_groups'],1)
    def test_nominal_filter_selects_only_actual_named_responses(self):
        s=self.survey(anonymous=False);self.add(s,employee_id=self.a.pk);self.add(s,reference='F2',employee_id=self.b.pk)
        self.assertEqual(service.summary([s],{self.a.pk})['received'],1)
    def test_simple_satisfaction_and_learning_are_not_modified_or_combined(self):
        from .models import DirectAssessment,AssessmentScale
        e=Enrollment.objects.get(group=self.g1,employee=self.a);AssessmentScale.objects.create(group=self.g1,initial_max=20,final_max=20)
        old=DirectAssessment.objects.create(enrollment=e,initial_points=10,final_points=15,satisfaction=3)
        s=self.survey();self.add(s,answers={'q01':5});old.refresh_from_db();self.assertEqual(old.satisfaction,3);self.assertEqual(old.final_points,15)
        self.assertEqual(service.summary([s])['dimensions'][0]['average'],5)
    def test_creation_view_fills_frozen_version_and_redirects(self):
        response=self.client.post(reverse('formation_survey_create'),dict(group=self.g1.pk,questionnaire=self.version.pk,evaluation_date='2026-01-04',mode='individual',anonymous='on'))
        self.assertEqual(response.status_code,302);self.assertEqual(FormationSurvey.objects.get().questionnaire_digest,self.version.schema_digest)
    def test_anonymous_post_cannot_smuggle_employee_identity(self):
        s=self.survey();response=self.client.post(reverse('formation_survey_entry',args=[s.pk]),dict(reference='F1',q01=5,state='completed',revision=0,employee=self.a.pk))
        self.assertEqual(response.status_code,200);self.assertFalse(s.responses.exists())
    def test_consultation_export_and_print_permissions(self):
        self.survey();user=get_user_model().objects.create_user('reader',is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=['view_training','view_traininggroup','view_formationsurvey']));self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('formation_survey_index')).status_code,200)
        self.assertEqual(self.client.get(reverse('formation_survey_index'),{'export':'xlsx'}).status_code,403)
        self.assertEqual(self.client.get(reverse('formation_survey_print')).status_code,403)
        self.assertEqual(self.client.get(reverse('formation_survey_create')).status_code,403)
    def test_filtered_results_and_dashboard_share_same_numbers(self):
        s=self.survey();self.add(s);response=self.client.get(reverse('formation_survey_index'),{'group':self.g1.pk})
        self.assertEqual(response.context['summary']['received'],1)
        dashboard=self.client.get(reverse('dashboard'));self.assertEqual(dashboard.context['formation_evaluation']['received'],1)
        self.assertEqual(dashboard.context['formation_evaluation']['dimensions'][0]['average'],5)
        self.assertEqual(self.client.get(reverse('formation_survey_index'),{'start':'2027-01-01'}).context['summary']['received'],0)
    def test_get_consultation_does_not_create_or_update_answers(self):
        s=self.survey();self.add(s);counts=(FormationSurveyResponse.objects.count(),AuditEvent.objects.count())
        self.client.get(reverse('formation_survey_index'));self.client.get(reverse('formation_survey_detail',args=[s.pk]))
        self.assertEqual(counts,(FormationSurveyResponse.objects.count(),AuditEvent.objects.count()))

    def test_empty_survey_can_correct_mode_before_first_entry(self):
        s=self.survey();s=service.configure_survey(self.user,s.pk,{'mode':'aggregate'},s.revision,'Escolha do método corrigida')
        self.assertEqual(s.mode,'aggregate');self.assertEqual(s.revision,2)
    def test_used_survey_cannot_silently_change_method_or_anonymity(self):
        s=self.survey();self.add(s)
        with self.assertRaises(ValidationError):service.configure_survey(self.user,s.pk,{'anonymous':False},s.revision,'Ensaio')
        with self.assertRaises(ValidationError):service.configure_survey(self.user,s.pk,{'mode':'aggregate'},s.revision,'Ensaio')
    def test_configuration_revision_and_reason_are_required(self):
        s=self.survey()
        with self.assertRaises(ValidationError):service.configure_survey(self.user,s.pk,{},0,'Motivo')
        with self.assertRaises(ValidationError):service.configure_survey(self.user,s.pk,{},1,'')
    def test_binary_form_exposes_no_intermediate_options(self):
        from .formation_survey_forms import EntryForm
        s=self.survey();form=EntryForm(s,self.user)
        self.assertEqual([v for v,label in form.fields['q04'].choices],['',1,5])
        self.assertNotIn('employee',form.fields)
    def test_invalid_export_is_rejected_without_server_error(self):
        self.assertEqual(self.client.get(reverse('formation_survey_index'),{'export':'docx'}).status_code,400)
    def test_recommendation_no_and_yes_are_a_separate_rate(self):
        s=self.survey('aggregate');self.aggregate(s,received=4,counts={'q04':[1,0,0,0,3]})
        result=service.summary([s]);self.assertEqual(result['recommendation']['yes_percent'],75);self.assertIsNone(result['dimensions'][0]['average'])
    def test_reference_like_missing_response_uses_23_not_24_forms(self):
        questions,_=question_metrics(SCHEMA,{'q02':[0,0,1,7,15]})
        self.assertEqual(questions[1]['n'],23);self.assertEqual(questions[1]['average'],Decimal(106)/23)
    def test_batch_without_personal_option_contains_no_names_or_contacts(self):
        self.survey();raw=form_pdf(self.user,'batch',self.g1);text=''.join(p.extract_text() for p in PdfReader(BytesIO(raw)).pages)
        self.assertNotIn(self.a.name,text);self.assertNotIn(self.a.email,text);self.assertNotIn(self.a.phone,text)
    def test_unknown_workplace_is_blank_instead_of_using_direction(self):
        self.survey(anonymous=False);raw=form_pdf(self.user,'person',self.g1,self.a.pk,True);text=PdfReader(BytesIO(raw)).pages[0].extract_text()
        self.assertNotIn(self.a.direction,text);self.assertNotIn('None',text)
    def test_group_with_survey_is_protected_from_deletion(self):
        from .planning import group_deletion_allowed
        self.survey();self.assertFalse(group_deletion_allowed(self.g1))

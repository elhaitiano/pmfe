from datetime import date,time
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.core.exceptions import ValidationError
from .models import Employee,Course,Training,ConsolidatedEdition,ConsolidatedParticipation,HistoricalInterfaceLink,Enrollment,EligibilityDecision,AuditEvent
from . import operational_status as policy
from .repeat_admission import preview,apply
from .scheduling import save_group

class OperationalStatusTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='OPS-SYN',name='Pessoa sintética')
        self.course=Course.objects.create(title='Curso operacional sintético')
        self.edition=ConsolidatedEdition.objects.create(source_key='ops-edition',course=self.course,title=self.course.title,start=date(2025,1,1),end=date(2025,1,1))
        self.p=ConsolidatedParticipation.objects.create(source_key='ops-participation',employee=self.person,edition=self.edition,completed=True,present_count=1,absent_count=0,attendance_complete=True,attendance_percent=100,attended_hours=7)
        self.t=Training.objects.create(employee=self.person,course=self.course,title=self.course.title,start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1,attendance_confirmed=True,status='Concluído')
        HistoricalInterfaceLink.objects.create(participation=self.p,training=self.t)
    def rule(self,**changes):
        data={'approval_reference':'REF-SYN','approved_by':'Responsável sintético','groups':['edition:'+str(self.edition.pk)],'non_classifying_documented':True,**changes}
        self.course.source_metadata={'_approved_group_validation_criteria':data};self.course.save()
    def item(self):return policy.rows(employee_id=self.person.pk)[0]
    def test_completion_requires_no_pdf(self):
        r=self.item();self.assertTrue(r['confirmed_completion']);self.assertEqual(r['certificate'],'Por verificar')
    def test_approved_nonclassifying_rule_proposes_certificate_without_pdf(self):
        self.rule();r=self.item();self.assertTrue(r['expected']);self.assertEqual(r['certificate'],'Previsto');self.t.refresh_from_db();self.assertIsNone(self.t.certified)
    def test_missing_rule_cannot_be_replaced_by_admin_conclusion(self):
        self.t.source_data={'_group_validation_v1':{'completion':'Concluído','academic':'Por validar'}};self.t.save();self.assertEqual(self.item()['expected'],0)
    def test_rule_for_other_group_does_not_apply(self):
        self.rule(groups=['edition:999999']);self.assertEqual(self.item()['expected'],0)
    def test_unapproved_rule_does_not_apply(self):
        self.rule(approval_reference='');self.assertEqual(self.item()['expected'],0)
    def test_missing_grade_is_not_failure(self):
        self.rule(non_classifying_documented=False,attendance_min_percent=80,components=[{'stage':'final','scale':20,'minimum':10}]);r=self.item();self.assertEqual(r['academic'],'Pendente de avaliação');self.assertFalse(r['expected']);self.assertNotEqual(r['completion'],'Não concluído')
    def test_minimum_grade_uses_approved_scale(self):
        self.rule(non_classifying_documented=False,attendance_min_percent=80,components=[{'stage':'final','scale':20,'minimum':10}]);self.p.grades=[{'stage':'final','value':'12','scale':'20'}];self.p.save();self.assertEqual(self.item()['expected'],1)
    def test_unknown_attendance_is_not_absence(self):
        self.p.present_count=None;self.p.attendance_complete=False;self.p.save();r=self.item();self.assertFalse(r['confirmed_completion']);self.assertNotIn('Ausência integral',' '.join(r['reasons']))
    def test_zero_presence_marks_review_and_preserves_delivery(self):
        self.p.present_count=0;self.p.attended_hours=0;self.p.save();self.t.certified=True;self.t.certificate_reference='HIST-SYN';self.t.save();r=self.item();self.assertFalse(r['confirmed_completion']);self.assertEqual(r['delivered'],1);self.assertEqual(r['certificate'],'Entregue')
    def test_unknown_hours_are_not_invented(self):
        self.p.attended_hours=None;self.p.save();self.assertFalse(self.item()['confirmed_completion']);self.assertIsNone(self.item()['hours'])
    def test_confirmed_source_issues_prevent_new_confirmation(self):
        self.p.evidence={'issues':['Divergência sintética']};self.p.save();self.assertFalse(self.item()['confirmed_completion'])
    def test_same_totals_across_training_and_canonical_paths(self):
        self.rule();self.assertEqual(policy.summary(policy.rows([self.t.pk])),policy.summary(policy.from_participations([self.p])))
    def test_review_candidates_remain_separate(self):
        other=Training.objects.create(employee=self.person,course=self.course,title=self.course.title,start=date(2025,1,1),end=date(2025,1,1))
        with patch('training.operational_status.review_index',return_value={str(self.t.pk):[{'related_training_ids':[other.pk]}],str(other.pk):[{'related_training_ids':[self.t.pk]}]}):r=policy.rows([self.t.pk,other.pk])
        self.assertEqual(len(r),2);self.assertTrue(all(x['review'] for x in r));self.assertEqual(Training.objects.count(),2)
    def test_all_business_data_unchanged_by_reading(self):
        before=list(Training.objects.values());canonical=list(ConsolidatedParticipation.objects.values());policy.rows();self.assertEqual(before,list(Training.objects.values()));self.assertEqual(canonical,list(ConsolidatedParticipation.objects.values()))
    def test_nonclassifying_rule_still_respects_approved_attendance_minimum(self):
        self.rule(attendance_min_percent=80);self.p.present_count=1;self.p.absent_count=1;self.p.attendance_percent=50;self.p.save();r=self.item();self.assertFalse(r['expected']);self.assertFalse(r['confirmed_completion'])
    def test_conflicting_percentage_does_not_create_expected_certificate(self):
        self.rule();self.p.attendance_percent=50;self.p.save();self.assertFalse(self.item()['expected'])
    def test_explicit_failed_result_is_not_silently_overridden(self):
        self.rule();self.p.result='Reprovado';self.p.save();r=self.item();self.assertFalse(r['expected']);self.assertIn('conflito',' '.join(r['reasons']))

class RepeatAdmissionTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='REPEAT-SYN',name='Pessoa sintética de reciclagem')
        self.course=Course.objects.create(title='Curso sintético de reciclagem',validation_status='validated',validation_notes='Referência sintética')
        self.group=save_group(self.course.pk,dict(start=date(2027,1,1),end=date(2027,1,1),dates=[date(2027,1,1)],trainer='Formador sintético',location='Sala sintética',capacity=2,hours_per_day=7,break_hours=1,start_time=time(8),end_time=time(16),planning_confirmation='Provisorio'))
        self.prior=Training.objects.create(employee=self.person,course=self.course,title='Título histórico anterior',start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1,attendance_confirmed=True,status='Concluído')
        self.user=get_user_model().objects.create_user('repeat-authorizer',is_staff=True)
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=['authorize_training_repeat','add_enrollment','view_employee','view_training']))
    def test_preview_writes_nothing(self):
        p=preview(self.group,self.person,self.user);self.assertEqual(p['employee'],self.person.pk);self.assertEqual(Enrollment.objects.count(),0);self.assertEqual(EligibilityDecision.objects.count(),0)
    def test_apply_adds_only_authorization_enrolment_and_audit(self):
        old=list(Training.objects.values());p=preview(self.group,self.person,self.user);entry=apply(self.group,self.person,self.user,p,'Reciclagem justificada','REF-SYN');self.assertEqual(Enrollment.objects.count(),1);self.assertIsNone(entry.training_id);self.assertEqual(EligibilityDecision.objects.count(),1);self.assertTrue(AuditEvent.objects.filter(action='REPEAT_ADMISSION_CONFIRMED').exists());self.assertEqual(old,list(Training.objects.values()))
    def test_replay_creates_no_second_enrolment(self):
        p=preview(self.group,self.person,self.user);apply(self.group,self.person,self.user,p,'Reciclagem justificada','REF-SYN')
        with self.assertRaises(ValidationError):apply(self.group,self.person,self.user,p,'Reciclagem justificada','REF-SYN')
        self.assertEqual(Enrollment.objects.count(),1);self.assertEqual(EligibilityDecision.objects.count(),1)
    def test_permissions_are_revalidated(self):
        p=preview(self.group,self.person,self.user);self.user.is_staff=False
        with self.assertRaises(ValidationError):apply(self.group,self.person,self.user,p,'Reciclagem justificada','REF-SYN')
        self.assertEqual(Enrollment.objects.count(),0)
    def test_reason_and_reference_required(self):
        p=preview(self.group,self.person,self.user)
        with self.assertRaises(ValidationError):apply(self.group,self.person,self.user,p,'','')
        self.assertEqual(EligibilityDecision.objects.count(),0)
    def test_changed_history_stops_confirmation(self):
        p=preview(self.group,self.person,self.user);self.prior.version+=1;self.prior.save()
        with self.assertRaises(ValidationError):apply(self.group,self.person,self.user,p,'Reciclagem justificada','REF-SYN')
        self.assertEqual(Enrollment.objects.count(),0)
    def test_staff_without_permissions_cannot_use_direct_url(self):
        user=get_user_model().objects.create_user('repeat-no-permission',is_staff=True);self.client.force_login(user)
        for method in [self.client.get,self.client.post]:self.assertEqual(method(reverse('repeat_admission',args=[self.group.pk])).status_code,403)
    def test_http_requires_preview_confirmation(self):
        self.client.force_login(self.user);response=self.client.post(reverse('repeat_admission',args=[self.group.pk]),{'employee':self.person.pk,'reason':'Reciclagem justificada','reference':'REF-SYN','action':'confirm','confirm':'on','token':'invalid'})
        self.assertEqual(response.status_code,409);self.assertEqual(Enrollment.objects.count(),0)
    def test_inactive_employee_cannot_be_admitted(self):
        self.person.registry_active=False;self.person.save()
        with self.assertRaises(ValidationError):preview(self.group,self.person,self.user)
    def test_csrf_is_required_for_confirmation(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.user);self.assertEqual(client.post(reverse('repeat_admission',args=[self.group.pk]),{'action':'confirm'}).status_code,403)
    def test_http_two_step_confirmation_creates_one_audited_admission(self):
        self.client.force_login(self.user);url=reverse('repeat_admission',args=[self.group.pk]);data={'employee':self.person.pk,'reason':'Reciclagem justificada','reference':'REF-SYN','action':'preview'}
        page=self.client.post(url,data);self.assertEqual(page.status_code,200);self.assertContains(page,'Pré-visualização da autorização');self.assertEqual(Enrollment.objects.count(),0)
        data.update(action='confirm',confirm='on',token=page.context['form']['token'].value());done=self.client.post(url,data);self.assertEqual(done.status_code,200);self.assertContains(done,'inscrição registada com auditoria');self.assertEqual(Enrollment.objects.count(),1);self.assertEqual(Training.objects.count(),1)
    def test_similar_title_in_other_course_cannot_authorize_repeat(self):
        other=Course.objects.create(title=self.course.title+' — outra acção');self.prior.course=other;self.prior.save()
        with self.assertRaises(ValidationError):preview(self.group,self.person,self.user)
    def test_conflicting_active_enrolment_in_another_group_is_blocked(self):
        other=save_group(self.course.pk,dict(start=date(2027,2,1),end=date(2027,2,1),dates=[date(2027,2,1)],trainer='Formador sintético',location='Sala sintética',capacity=2,hours_per_day=7,break_hours=1,start_time=time(8),end_time=time(16),planning_confirmation='Provisorio'))
        Enrollment.objects.create(group=other,employee=self.person)
        with self.assertRaises(ValidationError):preview(self.group,self.person,self.user)

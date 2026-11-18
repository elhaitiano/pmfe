from datetime import date
import json
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from .models import Employee, Course, Training, ConsolidatedEdition, ConsolidatedParticipation, HistoricalInterfaceLink, TrainingDocumentState, AuditEvent
from . import operational_status as policy, planning_delivery as delivery

@override_settings(PMF_DOCUMENTS_ENABLED=True)
class FinalDeliveryTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='FINAL-SYN',name='Formando sintético final')
        self.course=Course.objects.create(title='Formação final sintética')
        self.edition=ConsolidatedEdition.objects.create(source_key='final-edition',course=self.course,title=self.course.title,start=date(2025,1,1),end=date(2025,1,1))
        self.p=ConsolidatedParticipation.objects.create(source_key='final-participation',employee=self.person,edition=self.edition,completed=True,present_count=1,absent_count=0,attendance_complete=True,attendance_percent=100,attended_hours=7)
        self.t=Training.objects.create(employee=self.person,course=self.course,title=self.course.title,start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1,attendance_confirmed=True,status='Concluído',source_data={'certificate_eligible':True})
        HistoricalInterfaceLink.objects.create(participation=self.p,training=self.t)
        self.user=get_user_model().objects.create_user('final-delivery',is_staff=True)
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=['change_trainingdocumentstate','view_training','view_employee']))
        self.url=reverse('planning_certificate_delivery',args=[self.t.pk])+'?scope=edition&group='+str(self.edition.pk)
        self.client.force_login(self.user)
    def data(self):
        payload=delivery.snapshot(self.t,'edition',self.edition.pk,self.user)
        return {'date':'2025-02-01','responsible':'Responsável sintético','reference':'Acta administrativa SYN-1','justification':'Entrega administrativa comprovada','confirm':'on','token':signing.dumps(json.loads(json.dumps(payload,default=str)),salt=delivery.SALT)}
    def save(self,data=None):return delivery.confirm_delivery(self.user,self.t.pk,'edition',self.edition.pk,data or self.data())
    def test_eligible_card_metric_is_not_delivered_count(self):
        summary=policy.summary(policy.rows([self.t.pk]));self.assertEqual(summary['eligible'],1);self.assertEqual(summary['delivered'],0)
    def test_unhomologated_completion_is_not_automatically_eligible(self):
        self.t.source_data={};self.t.save();self.assertEqual(policy.summary(policy.rows([self.t.pk]))['eligible'],0)
    def test_proven_duplicate_not_counted_but_row_preserved(self):
        self.t.source_key='pilar3b:PF-00285';self.t.save();items=policy.rows([self.t.pk]);self.assertEqual(len(items),1);s=policy.summary(items);self.assertEqual(s['eligible'],0);self.assertEqual(s['proven_duplicates_excluded'],1);self.assertEqual(Training.objects.count(),1)
    def test_same_certificate_not_counted_twice(self):
        self.t.source_data={'certificate_eligible':True,'_certificate_summary':{'same_certificate_as':999,'issued_count':0,'delivered_count':0}};self.t.save();s=policy.summary(policy.rows([self.t.pk]));self.assertEqual(s['eligible'],0);self.assertEqual(s['certificate_duplicates_excluded'],1)
    def test_existing_state_does_not_restore_proven_duplicate_certificate_count(self):
        self.t.source_data={'certificate_eligible':True,'_certificate_summary':{'same_certificate_as':999,'issued_count':0,'delivered_count':0}};self.t.save()
        TrainingDocumentState.objects.create(training=self.t,issued=True,learner_delivered=True,learner_date=date(2025,1,15),learner_responsible='Responsável sintético',reference='REF-SYN')
        s=policy.summary(policy.rows([self.t.pk]));self.assertEqual(s['delivered'],0);self.assertEqual(s['issued'],0);self.assertEqual(s['eligible'],0)
    def test_get_is_readonly_and_identifies_exact_participation(self):
        old=list(Training.objects.values());r=self.client.get(self.url);self.assertEqual(r.status_code,200);self.assertContains(r,'TrainingID '+str(self.t.pk));self.assertEqual(old,list(Training.objects.values()));self.assertEqual(TrainingDocumentState.objects.count(),0)
    def test_delivery_without_pdf_changes_only_supplement_and_audit(self):
        old=list(Training.objects.values());canonical=list(ConsolidatedParticipation.objects.values());s=self.save();self.assertTrue(s.learner_delivered);self.assertEqual(s.reference,'Acta administrativa SYN-1');self.assertEqual(old,list(Training.objects.values()));self.assertEqual(canonical,list(ConsolidatedParticipation.objects.values()));self.assertTrue(AuditEvent.objects.filter(action='PLANNING_CERTIFICATE_DELIVERY_CONFIRMED').exists());self.assertEqual(policy.summary(policy.rows([self.t.pk]))['delivered'],1)
    def test_existing_certificate_metadata_does_not_hide_new_delivery(self):
        self.t.source_data={**self.t.source_data,'_certificate_summary':{'issued_count':1,'delivered_count':0}};self.t.save();self.save();self.assertEqual(policy.summary(policy.rows([self.t.pk]))['delivered'],1)
    def test_historical_delivery_cannot_be_overwritten(self):
        self.t.certified=True;self.t.certificate_delivery_date=date(2025,1,15);self.t.save();before=list(Training.objects.values())
        with self.assertRaises(ValidationError):self.save()
        self.assertEqual(before,list(Training.objects.values()));self.assertEqual(TrainingDocumentState.objects.count(),0)
    def test_replay_is_rejected(self):
        data=self.data();self.save(data)
        with self.assertRaises(ValidationError):self.save(data)
        self.assertEqual(TrainingDocumentState.objects.count(),1)
    def test_wrong_edition_is_rejected(self):
        with self.assertRaises(ValidationError):delivery.bound_training(self.t.pk,'edition',self.edition.pk+1)
    def test_changed_training_blocks_confirmation_and_rolls_back(self):
        data=self.data();self.t.version+=1;self.t.save()
        with self.assertRaises(ValidationError):self.save(data)
        self.assertEqual(TrainingDocumentState.objects.count(),0);self.assertEqual(AuditEvent.objects.filter(action='PLANNING_DELIVERY_PENDING').count(),0)
    def test_confirmation_and_reference_are_required(self):
        data=self.data();data.update(confirm='',reference='')
        with self.assertRaises(ValidationError):self.save(data)
        self.assertEqual(TrainingDocumentState.objects.count(),0)
    def test_future_delivery_rejected_without_changes(self):
        data=self.data();data['date']='2099-01-01'
        with self.assertRaises(ValidationError):self.save(data)
        self.assertEqual(TrainingDocumentState.objects.count(),0)
    def test_staff_without_permission_and_csrf_are_rejected(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.user);self.assertEqual(client.post(self.url,self.data()).status_code,403)
        other=get_user_model().objects.create_user('no-delivery-permission',is_staff=True);self.client.force_login(other);self.assertEqual(self.client.get(self.url).status_code,403)
    def test_http_confirmation_is_audited(self):
        old=list(Training.objects.values());canonical=list(ConsolidatedParticipation.objects.values())
        r=self.client.post(self.url,self.data());self.assertEqual(r.status_code,200);self.assertContains(r,'Entrega confirmada com referência e auditoria.');self.assertEqual(TrainingDocumentState.objects.count(),1)
        self.assertEqual(old,list(Training.objects.values()));self.assertEqual(canonical,list(ConsolidatedParticipation.objects.values()))
    def test_invalid_group_returns_400_without_writes(self):
        r=self.client.get(reverse('planning_certificate_delivery',args=[self.t.pk])+'?scope=edition&group=invalid');self.assertEqual(r.status_code,400);self.assertEqual(TrainingDocumentState.objects.count(),0)
    def test_existing_supplement_delivery_is_protected(self):
        self.save();old=list(TrainingDocumentState.objects.values())
        with self.assertRaises(ValidationError):self.save()
        self.assertEqual(old,list(TrainingDocumentState.objects.values()))

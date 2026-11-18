"""Derived labels must never promote formal approval or write audit/data."""
import json
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from .models import Course,AuditEvent,TrainingGroup,Employee,Training
from .catalogue_import import digest
from .catalogue_technical import attach,profile
from .planning import create_group

class TechnicalCatalogueTests(TestCase):
    def setUp(self):
        self.staff=get_user_model().objects.create_user('synthetic-technical-staff',is_staff=True)
        self.reader=get_user_model().objects.create_user('synthetic-technical-reader')
        meta={'id':'AC-2026-004','title':'Synthetic COP1','days':'10','hours':'6','origin':'Synthetic source','code':'COP1','area':''}
        self.course=Course.objects.create(title=meta['title'],source_system='epal-proagua-catalogo',source_action_id=meta['id'],source_metadata=meta,source_digest=digest(meta),source_internal_code='COP1',default_days=10,default_hours=6,revision=2,validation_status='pending')
        self.payload={'course_id':self.course.pk,'source_action_id':self.course.source_action_id,'after':{'default_days':10,'default_hours':'6.00','revision':2,'source_digest':self.course.source_digest},'justification':'Synthetic documented standard','reference':'Synthetic source section A'}
        self.event=AuditEvent.objects.create(actor='synthetic',action='CATALOGUE_CORRECTION',detail=json.dumps(self.payload))

    def technical(self):return attach([Course.objects.get(pk=self.course.pk)])[0].technical

    def test_documented_duration_and_title_are_scoped_not_formal_approval(self):
        t=self.technical();self.assertTrue(t['core_verified']);self.assertFalse(t['all_technical_fields_complete']);self.assertFalse(t['formal_approval_proven']);self.assertEqual(t['duration_total'],60)
        self.assertIn('Código interno aprovado por confirmar',t['pending']);self.assertIn('Área de formação por definir',t['pending'])

    def test_no_audit_no_verification_even_with_filled_duration(self):
        self.event.delete();self.assertFalse(self.technical()['core_verified'])

    def test_digest_mismatch_and_title_mismatch_fail_closed(self):
        self.course.source_digest='0'*64;self.course.save();self.assertFalse(self.technical()['core_verified'])

    def test_revision_mismatch_invalidates_old_evidence(self):
        self.course.revision=3;self.course.save();self.assertFalse(self.technical()['duration_verified'])

    def test_duration_change_cannot_reuse_old_approval(self):
        self.course.default_hours=7;self.course.save();self.assertFalse(self.technical()['duration_verified'])

    def test_missing_hours_are_unknown_not_zero(self):
        self.course.default_hours=None;self.course.save();self.assertIsNone(self.technical()['duration_total'])

    def test_audit_does_not_override_existing_duration_validators(self):
        self.course.default_hours=25;self.course.save()
        p={**self.payload,'after':{**self.payload['after'],'default_hours':'25'}}
        self.event.detail=json.dumps(p);self.event.save()
        self.assertFalse(self.technical()['duration_verified'])

    def test_blocked_designation_cannot_get_verified_badge(self):
        self.course.validation_status='blocked';self.course.save();self.assertFalse(self.technical()['core_verified'])

    def test_formal_pending_and_group_gate_preserved(self):
        self.assertTrue(self.technical()['core_verified']);self.course.refresh_from_db();self.assertEqual(self.course.validation_status,'pending');self.assertFalse(self.course.can_create_groups)
        self.client.force_login(self.staff);self.assertEqual(self.client.get(reverse('group_create',args=[self.course.pk])).status_code,302);self.assertEqual(TrainingGroup.objects.count(),0)

    def test_direct_group_service_still_requires_formal_validation(self):
        with self.assertRaises(ValidationError):create_group(self.course,{'dates':[date(2026,1,1)],'trainer':'Synthetic trainer','provider':'Synthetic provider','location':'Synthetic local','capacity':10,'hours_per_day':6,'break_hours':0,'start_time':'08:00','end_time':'14:00'})
        self.assertEqual(TrainingGroup.objects.count(),0)

    def test_permissions_unchanged(self):
        url=reverse('course_edit',args=[self.course.pk]);self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.reader);self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.staff);self.assertEqual(self.client.get(url).status_code,200)

    def test_rendered_fields_remain_explicit_and_no_create_button(self):
        self.client.force_login(self.staff);response=self.client.get(reverse('course_edit',args=[self.course.pk]));self.assertContains(response,'Dados verificados');self.assertContains(response,'Estado formal registado');self.assertContains(response,'60 H');self.assertContains(response,'Código interno aprovado por confirmar');self.assertContains(response,'Área de formação por definir')
        response=self.client.get(reverse('planning'));self.assertNotContains(response,'href="'+reverse('group_create',args=[self.course.pk])+'"')

    def test_get_classification_preserves_audit_and_historical_rows(self):
        employee=Employee.objects.create(number='SYN',name='Synthetic person')
        Training.objects.create(employee=employee,title='Synthetic history',start=date(2026,1,1),end=date(2026,1,1),days=1,hours_per_day=7,attendance=0)
        before=list(Training.objects.values());audit=list(AuditEvent.objects.values());catalogue=list(Course.objects.values())
        self.client.force_login(self.staff);self.client.get(reverse('planning'));self.client.get(reverse('course_edit',args=[self.course.pk]))
        self.assertEqual(before,list(Training.objects.values()));self.assertEqual(audit,list(AuditEvent.objects.values()));self.assertEqual(catalogue,list(Course.objects.values()))

    def test_evidence_loaded_once_for_multiple_cards(self):
        with self.assertNumQueries(1):rows=attach([self.course,self.course])
        self.assertEqual(len(rows),2)

    def test_duration_batch_evidence_supported(self):
        self.event.delete();AuditEvent.objects.create(actor='synthetic',action='DURATION_ACTION',detail=json.dumps({'pk':self.course.pk,'source_action_id':self.course.source_action_id,'days_after':10,'hours_after':'6','revision_after':2,'reference':'Synthetic source','reason':'Synthetic daily interpretation'}))
        self.assertTrue(self.technical()['core_verified'])

    def test_latest_conflicting_audit_does_not_fall_back_to_old_event(self):
        p={**self.payload,'after':{**self.payload['after'],'default_hours':None,'revision':3}}
        AuditEvent.objects.create(actor='synthetic',action='CATALOGUE_CORRECTION',detail=json.dumps(p));self.assertFalse(self.technical()['duration_verified'])

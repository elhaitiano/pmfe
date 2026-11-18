import hashlib,io,json,tempfile
import copy
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase,override_settings,Client
from django.contrib.auth.models import User,Permission
from django.core.exceptions import ValidationError,PermissionDenied,ImproperlyConfigured
from django.urls import reverse
from .models import Employee,Training,AuditEvent
from . import data_validation as v

class ValidationPolicyTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.employee=Employee.objects.create(number='SYNTHETIC-001',name='Trabalhador de teste')
        self.good=Training.objects.create(employee=self.employee,title='Curso sintético',start=date(2025,1,1),end=date(2025,1,2),days=2,hours_per_day=6,attendance=0,attendance_confirmed=True,status='Concluído',source_key='demo:PR-001')
        self.pending=Training.objects.create(employee=self.employee,title='Outro curso sintético',start=date(2025,2,1),end=date(2025,2,2),days=2,hours_per_day=6,attendance=2,attendance_confirmed=True,status='Concluído',source_key='demo:PF-002')
        self.admin=User.objects.create_superuser('test-reviewer','reviewer@example.invalid','synthetic-only')
        self.user=User.objects.create_user('test-reader',password='synthetic-only')
        self.base={'schema':1,'records':{}}
        raw={k:v.raw_records(k) for k in v.TABLES}
        employee_hash=v.digest(raw['employee'][self.employee.pk])
        state=lambda s:{'state':s,'evidence':'Documento sintético / linha 1'}
        self.base['records'][f'employee:{self.employee.pk}']={'fingerprint':employee_hash,'states':{k:state('confirmed') for k in v.DIMENSIONS['employee']}}
        for t in [self.good,self.pending]:
            self.base['records'][f'training:{t.pk}']={'fingerprint':v.digest(raw['training'][t.pk]),'employee_fingerprint':employee_hash,'dependencies':{k:v.digest(None) for k in ['evaluation','issued','learner_delivered','hr_delivered']},'states':{k:state('confirmed' if t==self.good else 'pending') for k in v.DIMENSIONS['training']}}
        self.path=Path(self.tmp.name)/'policy.json';self.save_manifest()
        from django.conf import settings
        templates=copy.deepcopy(settings.TEMPLATES)
        templates[0]['OPTIONS']['context_processors'].append('training.data_validation.context')
        self.override=override_settings(PMF_VALIDATION_ENABLED=True,PMF_VALIDATION_MANIFEST=str(self.path),PMF_VALIDATION_MANIFEST_SHA256=self.sha,MIDDLEWARE=[*settings.MIDDLEWARE,'training.data_validation.ValidationMiddleware'],TEMPLATES=templates)
        self.override.enable();self.addCleanup(self.override.disable)

    def save_manifest(self):
        body=json.dumps(self.base).encode();self.path.write_bytes(body);self.sha=hashlib.sha256(body).hexdigest()
    def change_state(self,kind,pk,dimension,state):
        self.base['records'][f'{kind}:{pk}']['states'][dimension]={'state':state,'evidence':'Prova sintética'};self.save_manifest()
        from django.conf import settings
        settings.PMF_VALIDATION_MANIFEST_SHA256=self.sha
    def proposal(self,**kw):
        return v.preview(self.admin,'training',self.pending.pk,'participation',kw.get('state','confirmed'),'Revisão sintética','Documento sintético')[0]

    def test_default_off_preserves_existing_counts(self):
        with override_settings(PMF_VALIDATION_ENABLED=False):self.assertEqual(len(v.official([self.good,self.pending])),2)
    def test_pending_separated_without_deleting(self):
        with v.scope():self.assertEqual([t.pk for t in v.official([self.good,self.pending])],[self.good.pk])
        self.assertEqual(Training.objects.count(),2)
    def test_documentary_participation_does_not_confirm_attendance(self):
        self.change_state('training',self.good.pk,'attendance','pending')
        from .analytics import confirmed
        with v.scope():self.assertTrue(v.policy().participation(self.good.pk));self.assertFalse(confirmed(self.good))
    def test_zero_is_confirmed_when_explicitly_documented(self):
        from .analytics import statistics
        with v.scope():self.assertEqual(statistics([self.good,self.pending])['attendance'],Decimal('0.0'))
    def test_absent_manifest_fails_closed(self):
        with override_settings(PMF_VALIDATION_MANIFEST=None):self.assertEqual(v.official([self.good]),[])
    def test_hash_tamper_rejected(self):
        self.path.write_text('{}')
        with self.assertRaises(ImproperlyConfigured):v.Policy()
    def test_changed_row_invalidates_evidence(self):
        Training.objects.filter(pk=self.good.pk).update(attendance=1)
        self.assertFalse(v.Policy().participation(self.good.pk))
    def test_changed_employee_invalidates_training_binding(self):
        Employee.objects.filter(pk=self.employee.pk).update(category='Categoria alterada')
        self.assertFalse(v.Policy().participation(self.good.pk))
    def test_confirm_audits_without_changing_training(self):
        before=Training.objects.values().get(pk=self.pending.pk);event=v.confirm(self.admin,self.proposal())
        self.assertEqual(event.action,v.ACTION);self.assertEqual(Training.objects.values().get(pk=self.pending.pk),before)
        self.assertIn('before',json.loads(event.detail))
    def test_replay_rejected_and_no_extra_audit(self):
        token=self.proposal();v.confirm(self.admin,token)
        with self.assertRaises(ValidationError):v.confirm(self.admin,token)
        self.assertEqual(AuditEvent.objects.filter(action=v.ACTION).count(),1)
    def test_token_user_bound(self):
        with self.assertRaises(PermissionDenied):v.confirm(self.user,self.proposal())
    def test_permission_staff_alone_is_insufficient(self):
        self.user.is_staff=True;self.user.save()
        with self.assertRaises(PermissionDenied):v.preview(self.user,'training',self.good.pk,'participation','confirmed','Motivo','Fonte')
    def test_evidence_and_reason_mandatory(self):
        with self.assertRaises(ValidationError):v.preview(self.admin,'training',self.good.pk,'attendance','confirmed','','')
    def test_tampered_token_rejected(self):
        with self.assertRaises(ValidationError):v.confirm(self.admin,self.proposal()+'tamper')
    def test_expired_token_rejected(self):
        with patch('django.core.signing.time.time',return_value=100):token=self.proposal()
        with self.assertRaises(ValidationError):v.confirm(self.admin,token)
    def test_stale_proposal_rolls_back_marker(self):
        token=self.proposal();Training.objects.filter(pk=self.pending.pk).update(version=2)
        with self.assertRaises(ValidationError):v.confirm(self.admin,token)
        self.assertEqual(AuditEvent.objects.filter(action=v.ACTION).count(),0)
    def test_new_decision_reverses_prior_decision_without_deleting(self):
        v.confirm(self.admin,self.proposal());v.confirm(self.admin,self.proposal(state='pending'))
        self.assertEqual(v.Policy().decision('training',self.pending.pk,'participation')['state'],'pending');self.assertEqual(AuditEvent.objects.filter(action=v.ACTION).count(),2)
    def test_administration_direct_request_permissions(self):
        self.client.force_login(self.user);self.assertEqual(self.client.get(reverse('validation_index')).status_code,403)
    def test_csrf_required(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.admin)
        self.assertEqual(client.post(reverse('validation_decision',args=['training',self.pending.pk]),{'state':'confirmed'}).status_code,403)
    def test_server_requires_explicit_confirmation(self):
        self.client.force_login(self.admin)
        response=self.client.post(reverse('validation_decision',args=['training',self.pending.pk]),{'token':self.proposal()})
        self.assertEqual(response.status_code,200);self.assertEqual(AuditEvent.objects.filter(action=v.ACTION).count(),0)
        self.assertContains(response,self.pending.title)
    def test_duplicate_requires_bound_evidence(self):
        self.assertFalse(v.Policy().duplicate(self.good.pk))
    def test_display_never_changes_original(self):
        self.change_state('training',self.good.pk,'evaluation','pending');Training.objects.filter(pk=self.good.pk).update(grade=15)
        self.good.refresh_from_db();shown=v.display(self.good)
        with v.scope():self.assertIsNone(shown.grade)
        self.assertEqual(self.good.grade,15)
    def test_dashboard_profile_consultation_have_same_totals(self):
        from .analytics import statistics
        from .dashboard import executive_data
        from .consultations import select,summary
        with v.scope():
            rows=select({});a=statistics([self.good,self.pending]);b=executive_data([self.good,self.pending],[self.employee]);c=summary(rows)
            self.assertEqual(a['total'],b['stats']['total']);self.assertEqual(a['total'],c['stats']['total'])
    def test_exports_pdf_xlsx_and_profile_render_without_pending(self):
        self.client.force_login(self.admin)
        for path in [reverse('employee_detail',args=[self.employee.pk]),reverse('employee_print',args=[self.employee.pk]),reverse('dashboard'),reverse('consultations')+'?format=xlsx',reverse('consultations')+'?format=pdf']:
            response=self.client.get(path);self.assertEqual(response.status_code,200,path)
            if 'format=' in path:
                body=b''.join(response.streaming_content);self.assertTrue(body.startswith(b'PK') if 'xlsx' in path else body.startswith(b'%PDF'))
    def test_integrity_after_decisions(self):
        v.confirm(self.admin,self.proposal())
        from django.db import connection
        with connection.cursor() as c:c.execute('PRAGMA foreign_key_check');self.assertEqual(c.fetchall(),[])
    def test_certificate_update_invalidates_previous_evidence(self):
        from .models import TrainingDocumentState
        TrainingDocumentState.objects.create(training=self.good,issued=True,reference='Documento sintético novo')
        self.assertFalse(v.Policy().fact(self.good.pk,'issued'))
    def test_evaluation_event_invalidates_previous_evidence(self):
        AuditEvent.objects.create(action='EVALUATION_CORRECTION',actor='test',detail=json.dumps({'training_id':self.good.pk}))
        self.assertFalse(v.Policy().fact(self.good.pk,'evaluation'))
    def test_no_staff_permission_to_confirm_employee(self):
        self.user.user_permissions.add(Permission.objects.get(codename='change_employee'),Permission.objects.get(codename='view_employee'))
        with self.assertRaises(PermissionDenied):v.preview(self.user,'employee',self.employee.pk,'identity','confirmed','Motivo','Documento')
    def test_uncertain_completion_does_not_claim_one_hundred_percent(self):
        self.change_state('training',self.good.pk,'completion','pending')
        from .executive import analyse
        with v.scope():
            data=analyse([self.good],[self.employee],{},None,None,'Teste')
            self.assertIsNone(data['completion_rate'])
    def test_proven_duplicate_excluded_once_but_preserved(self):
        self.pending.title=self.good.title;self.pending.start=self.good.start;self.pending.end=self.good.end;self.pending.save()
        record=self.base['records'][f'training:{self.pending.pk}'];record['fingerprint']=v.digest(v.raw_records('training')[self.pending.pk]);record['states']['participation']={'state':'confirmed','evidence':'Documento de duplicação sintético'}
        record['duplicate']={'of':self.good.pk,'peer_fingerprint':self.base['records'][f'training:{self.good.pk}']['fingerprint'],'evidence':'Consolidação sintética documentada'};self.save_manifest()
        from django.conf import settings
        settings.PMF_VALIDATION_MANIFEST_SHA256=self.sha
        with v.scope():
            self.assertEqual(len(v.official([self.good,self.pending])),1);self.assertTrue(v.policy().duplicate(self.pending.pk));self.assertEqual(v.summary([self.good,self.pending])['duplicates'],1)
        self.assertEqual(Training.objects.count(),2)
    def test_dependency_changed_between_preview_and_confirmation_rolls_back(self):
        token=v.preview(self.admin,'training',self.good.pk,'issued','confirmed','Documento','Prova')[0]
        from .models import TrainingDocumentState
        TrainingDocumentState.objects.create(training=self.good,reference='Outra prova')
        with self.assertRaises(ValidationError):v.confirm(self.admin,token)
        self.assertEqual(AuditEvent.objects.filter(action=v.ACTION).count(),0)
    def test_unconfirmed_worker_profile_restricted(self):
        self.change_state('employee',self.employee.pk,'identity','divergent');self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('employee_detail',args=[self.employee.pk])).status_code,403)
    def test_notice_and_states_render_in_html_and_print(self):
        self.client.force_login(self.admin)
        for name in ['employee_detail','employee_print']:
            response=self.client.get(reverse(name,args=[self.employee.pk]))
            self.assertContains(response,'Verificação dos dados');self.assertContains(response,'Categoria profissional')
    def test_categories_are_marked_without_modifying_employee(self):
        self.employee.category='Categoria sintética';self.employee.save();self.change_state('employee',self.employee.pk,'category','pending')
        with v.scope():self.assertIn('por validar',v.display_employee(self.employee).category)
        self.assertEqual(Employee.objects.get(pk=self.employee.pk).category,'Categoria sintética')
    def test_unverified_issuance_hidden_in_document_cards(self):
        from .models import TrainingDocumentState
        from .documents import delivery_summary
        TrainingDocumentState.objects.create(training=self.good,issued=True,reference='Prova nova não revista')
        with v.scope():self.assertEqual(delivery_summary(self.good)['issued'],'Por confirmar')
    def test_endings_exclude_completion_pending(self):
        self.change_state('training',self.good.pk,'completion','pending')
        from .executive import analyse
        with v.scope():self.assertEqual(analyse([self.good],[self.employee],{},None,None,'Teste')['ended'],[])
    def test_not_applicable_is_explicit_and_requires_evidence(self):
        token=v.preview(self.admin,'training',self.good.pk,'evaluation','not_applicable','Formação não sujeita a avaliação','Regulamento sintético')[0]
        v.confirm(self.admin,token)
        self.assertEqual(v.Policy().decision('training',self.good.pk,'evaluation')['state'],'not_applicable')
    def test_pending_summary_matches_exported_scope(self):
        from .executive import base_query,analyse
        from .consultations import select
        with v.scope():
            base,employees=base_query({});data=analyse(list(base),list(employees),{},None,None,'Teste',base)
            quality=v.summary(select({},include_pending=True))
            self.assertEqual(data['validation_summary']['pending'],quality['pending']);self.assertEqual(quality['pending'],1)


    def test_worker_selector_same_population_search_and_separate_pending(self):
        people=Employee.objects.bulk_create([Employee(number=f'SYNTH-D-{i:04}',name=f'Pessoa sintetica {i:04}') for i in range(1533)])
        raw=v.raw_records('employee')
        for i,e in enumerate(people):
            self.base['records'][f'employee:{e.pk}']={'fingerprint':v.digest(raw[e.pk]),'states':{
                k:{'state':('divergent' if i==1532 else 'confirmed') if k=='identity' else ('confirmed' if i<1517 else 'pending'),'evidence':'Fonte sintetica Fase 2D'} for k in v.DIMENSIONS['employee']}}
        self.save_manifest()
        with override_settings(PMF_VALIDATION_MANIFEST_SHA256=self.sha):
            self.client.force_login(self.admin)
            before=list(Training.objects.values());events=AuditEvent.objects.count()
            home=self.client.get(reverse('home'));selector=self.client.get(reverse('employee_data_select'))
            self.assertEqual(home.context['page'].paginator.count,1518)
            self.assertEqual(selector.context['page'].paginator.count,1518)
            for scope,count in [('pending',16),('pending_historical',15),('pending_conflict',1)]:
                response=self.client.get(reverse('employee_data_select'),{'scope':scope})
                self.assertEqual(response.context['page'].paginator.count,count)
                self.assertContains(response,'Cadastro actual:')
                self.assertContains(response,'não altera estados')
            for q in ['SYNTH-D-0000','Pessoa sintetica 0000']:
                self.assertEqual(self.client.get(reverse('home'),{'q':q}).context['page'].paginator.count,1)
                self.assertEqual(self.client.get(reverse('employee_data_select'),{'q':q}).context['page'].paginator.count,1)
            self.assertEqual(self.client.get(reverse('employee_data_select'),{'q':'SYNTH-D-1532'}).context['page'].paginator.count,0)
            self.assertEqual(self.client.get(reverse('employee_data_select'),{'scope':'pending_conflict','q':'SYNTH-D-1532'}).context['page'].paginator.count,1)
            self.assertEqual(list(Training.objects.values()),before)
            self.assertEqual(AuditEvent.objects.count(),events)
            self.assertEqual(Employee.objects.count(),1534)

    def test_worker_pending_selector_permission_enforced_for_direct_get(self):
        self.client.force_login(self.user)
        for scope in ['pending','pending_historical','pending_conflict']:
            self.assertEqual(self.client.get(reverse('employee_data_select'),{'scope':scope}).status_code,403)
        self.assertEqual(self.client.get(reverse('employee_data_select'),{'scope':'unknown'}).status_code,404)
        self.user.is_staff=True;self.user.save()
        self.assertEqual(self.client.get(reverse('employee_data_select'),{'scope':'pending'}).status_code,403)

    def test_worker_selector_requires_authentication(self):
        self.assertEqual(self.client.get(reverse('employee_data_select')).status_code,302)

    def test_worker_selector_legacy_mode_remains_unchanged(self):
        self.change_state('employee',self.employee.pk,'roster','pending')
        self.client.force_login(self.admin)
        with override_settings(PMF_VALIDATION_ENABLED=False):
            response=self.client.get(reverse('employee_data_select'))
            self.assertEqual(response.context['page'].paginator.count,1)
            self.assertNotContains(response,'Todos os pendentes')
            self.assertEqual(self.client.get(reverse('employee_data_select'),{'scope':'pending'}).status_code,403)

    def test_worker_selector_pagination_keeps_search_and_scope_without_js(self):
        people=Employee.objects.bulk_create([Employee(number=f'SYNTH-P-{i}',name=f'Pessoa pendente {i}') for i in range(35)])
        self.client.force_login(self.admin)
        response=self.client.get(reverse('employee_data_select'),{'scope':'pending','q':'Pessoa pendente','page':2})
        self.assertEqual(response.context['page'].paginator.count,35)
        self.assertContains(response,'scope=pending&amp;q=Pessoa%20pendente&amp;page=1',html=False)
        self.assertContains(response,'name="scope" value="pending"',html=False)

    def test_profile_history_print_keep_participation_without_attendance(self):
        self.change_state('training',self.good.pk,'attendance','pending')
        self.client.force_login(self.admin)
        for route in ['employee_data','employee_detail','employee_print']:
            response=self.client.get(reverse(route,args=[self.employee.pk]))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.context['stats']['total'],1)
            self.assertEqual(response.context['stats']['hours'],0)
            self.assertIsNone(response.context['stats']['attendance'])
            self.assertContains(response,self.good.title)
        self.good.refresh_from_db();self.assertEqual(self.good.attendance,0)

    def test_pending_professional_values_marked_in_profile_and_print(self):
        self.employee.category='Categoria sintetica RH';self.employee.function='Funcao sintetica RH';self.employee.save()
        raw=v.raw_records('employee')[self.employee.pk]
        self.base['records'][f'employee:{self.employee.pk}']['fingerprint']=v.digest(raw)
        for dim in ['category','function']:self.change_state('employee',self.employee.pk,dim,'pending')
        self.client.force_login(self.admin)
        for route in ['employee_data','employee_detail','employee_print']:
            response=self.client.get(reverse(route,args=[self.employee.pk]))
            self.assertContains(response,'Categoria sintetica RH (por validar)')
            if route!='employee_detail':self.assertContains(response,'Funcao sintetica RH (por validar)')
        self.employee.refresh_from_db();self.assertEqual(self.employee.category,'Categoria sintetica RH')

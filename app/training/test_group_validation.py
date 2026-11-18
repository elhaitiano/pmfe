import copy,json
from datetime import date,time
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase,Client
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.urls import reverse
from .models import (Employee,Training,Course,ConsolidatedEdition,ConsolidatedParticipation,
                     HistoricalInterfaceLink,TrainingDocumentState,AuditEvent,TrainingGroup,Enrollment)
from . import group_validation as service
from .group_validation_metrics import metrics


class GroupValidationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('validator',password='synthetic',is_staff=True)
        self.reader=get_user_model().objects.create_user('reader',password='synthetic',is_staff=True)
        permission,_=Permission.objects.get_or_create(content_type=ContentType.objects.get_for_model(TrainingGroup),codename='validate_training_group',defaults={'name':'Validar grupos'})
        self.user.user_permissions.add(permission,*Permission.objects.filter(codename__in=['view_training','view_employee','change_training']))
        self.course=Course.objects.create(code='TEST-GROUP',title='Curso sintético')
        self.edition=ConsolidatedEdition.objects.create(source_key='synthetic-edition',course=self.course,
            course_reference=self.course.code,title=self.course.title,group_label='1',start=date(2026,1,1),end=date(2026,1,2))
        self.key=f'edition:{self.edition.pk}'
        self.client.force_login(self.user)
        self.url=reverse('group_validation')

    def member(self,n=1,delivered=True,complete=True,issues=None,grade=True,status='Concluído',sources=True):
        person=Employee.objects.create(number=f'SYN-{n}',name=f'Pessoa sintética {n}')
        t=Training.objects.create(employee=person,course=self.course,title=self.course.title,start=date(2026,1,1),end=date(2026,1,2),
            days=2,hours_per_day=7,attendance=2 if complete else None,attendance_confirmed=complete,status=status,
            grade=Decimal('16') if grade else None,certificate_issued=True if delivered else None,certified=True if delivered else None,
            certificate_reference='SYNTHETIC' if delivered else '',source_data={'preserved':'original'})
        p=ConsolidatedParticipation.objects.create(employee=person,edition=self.edition,source_key=f'synthetic-{n}',
            completed=True if status=='Concluído' else None,present_count=2 if complete else None,absent_count=0 if complete else None,
            attendance_complete=complete,attendance_percent=100 if complete else None,
            grades=[{'stage':'final','value':'80','scale':'100'}] if grade else [],
            evidence={'sources':[{'source':'synthetic-evidence'}] if sources else [],'issues':issues or []})
        HistoricalInterfaceLink.objects.create(training=t,participation=p)
        if delivered:TrainingDocumentState.objects.create(training=t,issued=True,learner_delivered=True,reference='synthetic-delivery',historical_delivery_basis='synthetic evidence')
        return t,p

    def review(self):return service.preview(self.user,[self.key])
    def apply(self):return service.apply(self.user,self.review()['token'],True)
    def rule(self,minimum='60',attendance='90'):
        self.course.source_metadata={'_approved_group_validation_criteria':{
            'approval_reference':'synthetic fixture only','approved_by':'synthetic authority','groups':[self.key],
            'attendance_min_percent':attendance,'components':[{'stage':'final','scale':'100','minimum':minimum}]}}
        self.course.save()

    def test_preview_read_only_and_exact_changes(self):
        t,p=self.member();before=service.values(t);events=AuditEvent.objects.count()
        response=self.client.get(self.url,{'group':self.key})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Confirmar validação colectiva')
        self.assertContains(response,'Critério de aprovação não definido')
        self.assertEqual(response.context['groups'][0]['eligible'],1)
        t.refresh_from_db();self.assertEqual(service.values(t),before);self.assertEqual(AuditEvent.objects.count(),events)

    def test_delivered_without_grade_or_attendance_only_validates_administratively(self):
        t,p=self.member(grade=False,complete=False);self.apply();t.refresh_from_db();p.refresh_from_db()
        decision=t.source_data[service.MARKER]['decisions']
        self.assertEqual(decision,{'academic':'Por validar','completion':'Concluído','documentary':'Certificado entregue'})
        self.assertIsNone(t.grade);self.assertIsNone(t.attendance);self.assertTrue(t.certified);self.assertEqual(p.grades,[])
        self.assertTrue(TrainingDocumentState.objects.get(training=t).learner_delivered)

    def test_all_eligible_one_audit_each_and_original_fields_unchanged(self):
        records=[self.member(n)[0] for n in range(1,4)]
        before={t.pk:service.values(t) for t in records};self.assertEqual(len(self.apply()),3)
        self.assertEqual(AuditEvent.objects.filter(action=service.ACTION).count(),3)
        for t in records:
            t.refresh_from_db();after=service.values(t);old=before[t.pk]
            self.assertEqual(after['version'],old['version']+1)
            after.pop('version');old.pop('version');after['source_data'].pop(service.MARKER)
            self.assertEqual(after,old)
        event=json.loads(AuditEvent.objects.filter(action=service.ACTION).first().detail)
        self.assertEqual(event['actor_id'],self.user.pk);self.assertIn('before',event);self.assertIn('after',event)

    def test_mixed_group_excludes_contradictions_and_withdrawals(self):
        valid,_=self.member(1);conflict,_=self.member(2,issues=[{'field':'grade','reason':'different sources'}]);withdrawn,_=self.member(3,status='Desistência')
        self.assertEqual(self.review()['groups'][0]['inconsistent'],1)
        self.assertEqual(self.review()['groups'][0]['eligible'],1);self.apply()
        for t in (conflict,withdrawn):t.refresh_from_db();self.assertNotIn(service.MARKER,t.source_data)
        valid.refresh_from_db();self.assertIn(service.MARKER,valid.source_data)

    def test_missing_grade_under_explicit_rule_never_generates_approval(self):
        t,p=self.member(grade=False);self.rule();r=self.review()['groups'][0]['rows'][0]
        self.assertEqual(r['academic'],'Pendente de avaliação');self.assertTrue(any('Nota obrigatória ausente' in x for x in r['reasons']))
        self.apply();t.refresh_from_db();self.assertIsNone(t.grade)

    def test_unknown_attendance_not_zero_or_rejection(self):
        t,p=self.member(complete=False);self.rule();self.assertEqual(self.review()['groups'][0]['rows'][0]['academic'],'Pendente de avaliação')

    def test_real_rule_both_criteria_and_zero_grade(self):
        self.member(delivered=False);self.rule();self.assertEqual(self.review()['groups'][0]['rows'][0]['academic'],'Aprovado')
        p=ConsolidatedParticipation.objects.get();p.grades=[{'stage':'final','value':'0','scale':'100'}];p.save()
        self.assertEqual(self.review()['groups'][0]['rows'][0]['academic'],'Reprovado')

    def test_insufficient_attendance_preserves_delivered_certificate(self):
        t,p=self.member();self.rule();p.present_count=1;p.absent_count=1;p.attendance_percent=50;p.save()
        r=self.review()['groups'][0]['rows'][0];self.assertEqual(r['academic'],'Por validar')
        self.assertIn('Certificado entregue',r['changes']['documentary']);self.apply()
        t.refresh_from_db();self.assertTrue(t.certified);self.assertEqual(t.result,'Pendente')

    def test_rule_scoped_to_different_group_not_applied(self):
        self.member();self.rule();self.course.source_metadata['_approved_group_validation_criteria']['groups']=['edition:99999'];self.course.save()
        self.assertEqual(self.review()['groups'][0]['rows'][0]['academic'],'Por validar')

    def test_final_aggregate_does_not_replace_theory_and_practice(self):
        self.member();self.rule();self.course.source_metadata['_approved_group_validation_criteria']['components']=[{'stage':'theory','scale':100,'minimum':80},{'stage':'practice','scale':100,'minimum':80}];self.course.save()
        self.assertEqual(self.review()['groups'][0]['rows'][0]['academic'],'Pendente de avaliação')

    def test_no_completion_from_registration_or_final_day(self):
        self.member(delivered=False,status='Em Curso',grade=False,sources=False)
        group=self.review()['groups'][0];self.assertEqual(group['eligible'],0)
        self.assertEqual(group['inconsistent'],0)
        self.assertTrue(any('Conclusão sem evidência' in s for s in group['rows'][0]['reasons']))

    def test_repeated_preview_and_confirmation_idempotent(self):
        t,p=self.member();token=self.review()['token'];service.apply(self.user,token,True)
        with self.assertRaises(ValidationError):service.apply(self.user,token,True)
        self.assertEqual(self.apply(),[]);self.assertEqual(AuditEvent.objects.filter(action=service.ACTION).count(),1)

    def test_previous_decision_not_overwritten_after_correction(self):
        t,p=self.member();self.apply();t.refresh_from_db();marker=copy.deepcopy(t.source_data[service.MARKER])
        Training.objects.filter(pk=t.pk).update(grade=15)
        result=self.review()['groups'][0];self.assertEqual(result['eligible'],0);self.assertEqual(result['inconsistent'],1)
        self.apply();t.refresh_from_db();self.assertEqual(t.source_data[service.MARKER],marker)
        self.assertEqual(metrics([t])['review'],1)

    def test_stale_preview_for_unversioned_change(self):
        t,p=self.member();token=self.review()['token'];Training.objects.filter(pk=t.pk).update(grade=14)
        with self.assertRaises(ValidationError):service.apply(self.user,token,True)
        self.assertFalse(AuditEvent.objects.filter(action=service.ACTION).exists())

    def test_stale_presence_document_rule_membership_or_employee(self):
        t,p=self.member()
        for model,pk,field,value in [(ConsolidatedParticipation,p.pk,'attendance_percent',50),(TrainingDocumentState,TrainingDocumentState.objects.get(training=t).pk,'reference','changed'),(Employee,t.employee_id,'name','Changed')]:
            token=self.review()['token'];model.objects.filter(pk=pk).update(**{field:value})
            with self.assertRaises(ValidationError):service.apply(self.user,token,True)
        token=self.review()['token'];self.rule()
        with self.assertRaises(ValidationError):service.apply(self.user,token,True)
        token=self.review()['token'];self.member(2)
        with self.assertRaises(ValidationError):service.apply(self.user,token,True)

    def test_atomic_rollback_on_second_audit_failure(self):
        a,_=self.member(1);b,_=self.member(2);original=AuditEvent.objects.create
        def create(**kw):
            if json.loads(kw['detail'])['training_id']==b.pk:raise RuntimeError('synthetic failure')
            return original(**kw)
        with patch.object(AuditEvent.objects,'create',side_effect=create):
            with self.assertRaises(RuntimeError):self.apply()
        for t in (a,b):t.refresh_from_db();self.assertNotIn(service.MARKER,t.source_data)
        self.assertFalse(AuditEvent.objects.filter(action=service.ACTION).exists())

    def test_compare_and_swap_failure_rolls_back(self):
        self.member()
        with patch('django.db.models.query.QuerySet.update',return_value=0):
            with self.assertRaises(ValidationError):self.apply()
        self.assertFalse(AuditEvent.objects.filter(action=service.ACTION).exists())

    def test_staff_without_permission_service_and_all_methods_denied(self):
        self.member()
        with self.assertRaises(PermissionDenied):service.preview(self.reader,[self.key])
        self.client.force_login(self.reader)
        for method in ('get','post'):
            self.assertEqual(getattr(self.client,method)(self.url,{'group':self.key}).status_code,403)
        self.assertEqual(self.client.put(self.url).status_code,405)

    def test_permission_revoked_between_preview_and_confirmation(self):
        self.member();token=self.review()['token'];self.user.user_permissions.clear()
        user=get_user_model().objects.get(pk=self.user.pk)
        with self.assertRaises(PermissionDenied):service.apply(user,token,True)

    def test_specific_permission_without_staff_is_sufficient(self):
        self.member();self.user.is_staff=False;self.user.save();self.assertEqual(len(self.apply()),1)

    def test_wrong_user_tampered_expired_or_missing_confirmation(self):
        self.member();token=self.review()['token']
        with self.assertRaises(ValidationError):service.apply(self.user,token,False)
        with self.assertRaises(ValidationError):service.apply(self.user,token+'bad',True)
        payload=signing.loads(token,salt=service.SALT);payload['user']=self.reader.pk
        with self.assertRaises(PermissionDenied):service.apply(self.user,signing.dumps(payload,salt=service.SALT),True)
        with patch('django.core.signing.time.time',return_value=0):expired=self.review()['token']
        with self.assertRaises(ValidationError):service.apply(self.user,expired,True)

    def test_csrf_and_direct_post_and_extra_fields(self):
        self.member();c=Client(enforce_csrf_checks=True);c.force_login(self.user)
        self.assertEqual(c.post(self.url,{'action':'confirm','confirm':'yes','token':self.review()['token']}).status_code,403)
        self.assertEqual(self.client.post(self.url,{'action':'confirm','confirm':'yes'}).status_code,409)
        self.assertEqual(self.client.post(self.url,{'action':'confirm','confirm':'yes','token':self.review()['token'],'grade':20}).status_code,400)

    def test_post_uses_own_transaction_not_global_sync(self):
        t,p=self.member()
        with patch('training.interface_sync.reconcile',side_effect=AssertionError('unrelated global sync')):
            response=self.client.post(self.url,{'action':'confirm','confirm':'yes','token':self.review()['token']})
        self.assertEqual(response.status_code,302);t.refresh_from_db();self.assertIn(service.MARKER,t.source_data)

    def test_multigroup_preview_and_atomic_application(self):
        self.member(1);second=ConsolidatedEdition.objects.create(source_key='second',course=self.course,course_reference='SYN',title='Second')
        _,p=self.member(2);p.edition=second;p.save();key=f'edition:{second.pk}'
        data=service.preview(self.user,[self.key,key]);self.assertEqual(len(data['groups']),2);self.assertEqual(data['eligible'],2)
        self.assertEqual(len(service.apply(self.user,data['token'],True)),2)

    def test_duplicate_groups_invalid_keys_and_boundaries(self):
        for keys in ([self.key,self.key],[],['edition:bad'],['unknown:1'],[f'edition:{i}' for i in range(11)]):
            with self.assertRaises(ValidationError):service.preview(self.user,keys)

    def test_native_group_reuses_explicit_membership_and_excludes_only_enrolment(self):
        from .planning import create_group
        self.course.validation_status='validated';self.course.save()
        group=create_group(self.course,dict(dates=[date(2026,1,1),date(2026,1,2)],trainer='Synthetic',location='Synthetic',capacity=10,hours_per_day=Decimal(7),break_hours=Decimal(1),start_time=time(8),end_time=time(16)))
        t,p=self.member();Training.objects.filter(pk=t.pk).update(group=group)
        other=Employee.objects.create(number='SYN-NEW',name='Unconfirmed enrolment')
        Enrollment.objects.create(group=group,employee=other)
        data=service.preview(self.user,[f'group:{group.pk}'])['groups'][0]
        self.assertEqual(data['eligible'],1);self.assertEqual(len(data['pending']),1)
        self.assertEqual(data['total_enrolled'],2)
        with self.assertRaises(ValidationError):service.preview(self.user,[self.key,f'group:{group.pk}'])

    def test_dashboard_metrics_refresh_and_filter_and_no_inferred_approval(self):
        t,p=self.member();other,_=self.member(2);self.assertEqual(metrics([t])['validated'],0)
        self.apply();m=metrics([t]);self.assertEqual(m['completed'],1);self.assertEqual(m['approved'],0)
        self.assertEqual(m['undefined'],1);self.assertEqual(m['academic_denominator'],0)
        self.assertEqual(m['numeric_final'],1)
        self.assertEqual(m['total'],1);self.assertEqual(metrics([])['validated'],0)

    def test_missing_binding_and_document_reference_are_explicit_exceptions(self):
        t,p=self.member();HistoricalInterfaceLink.objects.filter(training=t).delete()
        g=self.review()['groups'][0];self.assertEqual(len(g['pending']),1);self.assertEqual(g['eligible'],0)

    def test_course_identity_conflict_is_excluded(self):
        t,p=self.member();other=Course.objects.create(code='OTHER',title='Other');Training.objects.filter(pk=t.pk).update(course=other)
        self.assertEqual(self.review()['groups'][0]['eligible'],0)

    def test_missing_certificate_correspondence_blocks_administrative_validation(self):
        t,p=self.member();TrainingDocumentState.objects.filter(training=t).update(reference='')
        Training.objects.filter(pk=t.pk).update(certificate_reference='')
        g=self.review()['groups'][0];self.assertEqual(g['eligible'],0)
        self.assertTrue(any('Certificado sem correspondência' in s for s in g['rows'][0]['reasons']))

    def test_inactive_employee_stale_decision_not_counted(self):
        t,p=self.member();token=self.review()['token'];Employee.all_objects.filter(pk=t.employee_id).update(registry_active=False)
        with self.assertRaises(ValidationError):service.apply(self.user,token,True)

    def test_permission_provision_command_does_not_assign_and_dry_run_is_readonly(self):
        from django.core.management import call_command
        count=Permission.objects.count();call_command('prepare_group_validation_permission',verbosity=0)
        self.assertEqual(Permission.objects.count(),count)
        before=self.reader.user_permissions.count();call_command('prepare_group_validation_permission',confirm_create_permission=True,verbosity=0)
        self.assertEqual(self.reader.user_permissions.count(),before)

    def test_existing_validated_evaluation_not_overwritten(self):
        from .models import TrainingEvaluation
        t,p=self.member();record=TrainingEvaluation.objects.create(training=t,validation='Validado',source='synthetic',evaluator='Synthetic',date=date(2026,1,3),validated_grade=16,validated_result='Aprovado')
        group=self.review()['groups'][0];self.assertEqual(group['validated'],1);self.assertEqual(group['eligible'],0)
        self.assertEqual(self.apply(),[]);record.refresh_from_db();self.assertEqual(record.validated_result,'Aprovado')

    def test_empty_previous_marker_is_not_overwritten(self):
        t,p=self.member();t.source_data[service.MARKER]={};t.save()
        self.assertEqual(self.review()['groups'][0]['eligible'],0);self.assertEqual(self.apply(),[])

    def test_explicit_failed_rule_without_certificate_is_not_approval(self):
        t,p=self.member(delivered=False);self.rule();p.present_count=1;p.absent_count=1;p.attendance_percent=50;p.save()
        row=self.review()['groups'][0]['rows'][0];self.assertEqual(row['academic'],'Reprovado');self.apply()
        t.refresh_from_db();self.assertIsNone(t.certified);self.assertEqual(t.result,'Pendente')

    def test_certificate_state_contradiction_excluded_preserving_delivery(self):
        t,p=self.member();TrainingDocumentState.objects.filter(training=t).update(issued=False)
        self.assertEqual(self.review()['groups'][0]['eligible'],0);self.assertEqual(self.apply(),[])
        t.refresh_from_db();self.assertTrue(t.certified)

    def test_row_limit_is_enforced_before_confirmation(self):
        self.member(1);self.member(2)
        with patch.object(service,'MAX_ROWS',1):
            with self.assertRaises(ValidationError):self.review()

    def test_preview_escapes_names_and_reasons(self):
        t,p=self.member();Employee.objects.filter(pk=t.employee_id).update(name='<script>alert(1)</script>')
        response=self.client.get(self.url,{'group':self.key})
        self.assertContains(response,'&lt;script&gt;alert(1)&lt;/script&gt;');self.assertNotContains(response,'<script>alert(1)</script>')

    def test_dashboard_batch_queries_do_not_grow_per_historical_group(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        records=[]
        for n in range(1,7):
            t,p=self.member(n);records.append(t)
            if n>1:
                edition=ConsolidatedEdition.objects.create(source_key=f'synthetic-edition-{n}',course=self.course,course_reference='SYN',title='Synthetic')
                p.edition=edition;p.save()
                key=f'edition:{edition.pk}'
            else:key=self.key
            data=service.preview(self.user,[key]);service.apply(self.user,data['token'],True)
        with CaptureQueriesContext(connection) as queries:result=metrics(records)
        self.assertEqual(result['validated'],6);self.assertEqual(result['numeric_final'],6);self.assertLessEqual(len(queries),7)

    def test_technical_sync_revision_does_not_invalidate_real_evidence(self):
        t,p=self.member();t.source_data['_EPAL_CONSOLIDATED_FACTS']={'sync_version':1,'participation':p.source_key};t.save()
        self.apply();t.refresh_from_db();t.source_data['_EPAL_CONSOLIDATED_FACTS']['sync_version']=t.version;t.save()
        self.assertEqual(metrics([t])['validated'],1);self.assertEqual(self.review()['groups'][0]['inconsistent'],0)

    def test_existing_signed_evaluation_and_verified_data_bindings_preserved(self):
        a,_=self.member(1);b,_=self.member(2)
        AuditEvent.objects.create(actor='synthetic',action='EPAL_EVALUATION_APPROVED',detail=json.dumps({'training_id':a.pk,'unit':'percentage','raw':'80'}))
        AuditEvent.objects.create(actor='synthetic',action='DATA_VERIFICATION',detail=json.dumps({'kind':'training','pk':b.pk,'dimension':'completion','state':'confirmed'}))
        before={t.pk:service.values(t) for t in (a,b)}
        self.assertEqual(self.review()['groups'][0]['validated'],2);self.assertEqual(self.apply(),[])
        for t in (a,b):t.refresh_from_db();self.assertEqual(service.values(t),before[t.pk])

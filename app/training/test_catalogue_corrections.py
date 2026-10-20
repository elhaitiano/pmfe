"""Synthetic fixtures only, including 2,057 independent historical participations."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from . import catalogue_corrections as service
from .catalogue_duration_batch import READY, SOURCE_SYSTEM
from .models import AuditEvent, Course, Employee, Enrollment, GroupSession, Training, TrainingGroup


class CorrectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('synthetic-corrector', is_staff=True)
        cls.user.user_permissions.add(Permission.objects.get(codename='change_course', content_type__app_label='training'))
        cls.staff = get_user_model().objects.create_user('synthetic-staff', is_staff=True)
        cls.reader = get_user_model().objects.create_user('synthetic-reader')
        for identifier in [f'AC-2026-{i:03}' for i in range(1,55)] + ['HC-RFTE','HC-RDSC','HC-FHDR']:
            days, hours = READY.get(identifier, (None, None))
            title = 'Synthetic ' + identifier
            if identifier == 'AC-2026-015':
                days, hours = 5, None
                title = 'Synthetic Excel 30h, 5 dias'
            metadata = {'title':title,'days':str(days) if days else None,
                        'hours':'7' if identifier=='AC-2026-015' else None,
                        'origin':'Synthetic document'}
            course = Course.objects.create(source_system=SOURCE_SYSTEM, source_action_id=identifier,
                title=title, default_days=days, default_hours=hours,
                revision=2 if identifier in READY else 1,
                source_metadata=metadata, source_digest=service.digest(metadata),
                source_internal_code='EXCE' if identifier=='AC-2026-015' else '',
                validation_status='blocked' if identifier=='HC-RDSC' else 'pending')
            if identifier=='AC-2026-015':cls.excel=course
            if identifier=='AC-2026-003':cls.girh=course
        employee = Employee.objects.create(number='synthetic-only',name='Synthetic fixture')
        Training.objects.bulk_create([Training(employee=employee,
            title='Synthetic Excel history' if i<17 else 'Synthetic GIRH history' if i<30 else 'Synthetic other history',
            days=2 if 17<=i<30 else 5, hours_per_day=7, attendance=1,
            start=date(2025,1,1),end=date(2025,1,5)) for i in range(2057)])
        AuditEvent.objects.bulk_create([AuditEvent(actor='Synthetic prior batch',action='PRIOR_FIXTURE',detail=str(i)) for i in range(34)])

    def setUp(self):
        self.client.force_login(self.user)
        self.url=reverse('course_correct',args=[self.excel.pk])
        self.history_before=service.digest(list(Training.objects.order_by('pk').values()))
        self.others=service.canonical(list(Course.objects.exclude(pk=self.excel.pk).order_by('pk').values()))
        self.old_audits=list(AuditEvent.objects.order_by('pk').values())

    def data(self,**changes):
        data={'revision':self.excel.revision,'days':5,'hours':'6',
              'correction_type':'documented_standard','justification':'Synthetic decision about future standard',
              'reference':'Synthetic document / responsible decision'}
        data.update(changes)
        return data

    def preview(self,**changes):
        response=self.client.post(self.url,{'action':'review',**self.data(**changes)})
        self.assertEqual(response.status_code,200,response.content[:500])
        self.assertIsNotNone(response.context['preview'])
        return response.context['confirmation'].initial['token']

    def apply(self,token):
        return self.client.post(self.url,{'action':'confirm','token':token,'confirmed':'on'})

    def preserved(self):
        self.assertEqual(Training.objects.count(),2057)
        self.assertEqual(service.digest(list(Training.objects.order_by('pk').values())),self.history_before)
        self.assertEqual(service.canonical(list(Course.objects.exclude(pk=self.excel.pk).order_by('pk').values())),self.others)
        for old in self.old_audits:
            self.assertEqual(AuditEvent.objects.values().get(pk=old['id']),old)
        for model in (TrainingGroup,Enrollment,GroupSession):self.assertFalse(model.objects.exists())

    def test_authentication_and_permissions_on_every_endpoint(self):
        token=self.preview()
        for user in (None,self.staff,self.reader):
            client=Client()
            if user:client.force_login(user)
            for url,data in [(self.url,None),(self.url,{'action':'review',**self.data()}),
                    (self.url,{'action':'confirm','token':token,'confirmed':'on'}),
                    (reverse('course_correction_reverse',args=[self.excel.pk,1]),None),
                    (reverse('course_correction_result',args=[self.excel.pk,1]),None)]:
                response=client.get(url) if data is None else client.post(url,data)
                self.assertEqual(response.status_code,302 if user is None else 403)
        self.assertEqual(AuditEvent.objects.count(),34)

    def test_button_only_for_authorised_excel(self):
        edit=reverse('course_edit',args=[self.excel.pk])
        self.assertContains(self.client.get(edit),'Corrigir dados')
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(edit),'href="'+self.url+'"')
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse('course_edit',args=[self.girh.pk])),'Corrigir dados</a>')

    def test_no_js_preview_is_read_only_and_confirmation_applies_exactly(self):
        before=service.course_state(self.excel)
        response=self.client.get(self.url)
        self.assertContains(response,'30 H')
        self.assertContains(response,'17 participações históricas de 35 horas')
        token=self.preview()
        self.assertEqual(service.course_state(self.excel),before)
        self.assertEqual(AuditEvent.objects.count(),34)
        response=self.apply(token)
        self.assertEqual(response.status_code,302)
        self.excel.refresh_from_db()
        self.assertEqual(self.excel.total_hours,Decimal('30'))
        after=service.course_state(self.excel)
        self.assertEqual(after['revision'],2)
        for k in before.keys()-{'default_days','default_hours','revision'}:self.assertEqual(after[k],before[k])
        self.assertEqual(AuditEvent.objects.filter(action=service.ACTION).count(),1)
        self.assertEqual(AuditEvent.objects.filter(action=service.BATCH_ACTION).count(),1)
        detail=json.loads(AuditEvent.objects.filter(action=service.ACTION).get().detail)
        self.assertEqual(detail['user_id'],self.user.pk)
        self.assertEqual(detail['before'],before)
        self.assertEqual(detail['after'],after)
        self.assertEqual(detail['evidence']['historical_participations'],17)
        self.assertEqual(detail['correction_type'],'documented_standard')
        self.assertContains(self.client.get(response.url),'Operação confirmada e auditada')
        self.preserved()

    def test_csrf_required_on_review_confirmation_and_reversal(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(self.url,{'action':'review',**self.data()}).status_code,403)
        client.get(self.url);csrf=client.cookies['csrftoken'].value
        review=client.post(self.url,{'action':'review','csrfmiddlewaretoken':csrf,**self.data()})
        token=review.context['confirmation'].initial['token']
        self.assertEqual(client.post(self.url,{'action':'confirm','token':token,'confirmed':'on'}).status_code,403)
        response=client.post(self.url,{'action':'confirm','token':token,'confirmed':'on','csrfmiddlewaretoken':csrf})
        self.assertEqual(response.status_code,302)
        event=AuditEvent.objects.get(action=service.ACTION)
        url=reverse('course_correction_reverse',args=[self.excel.pk,event.pk])
        self.assertEqual(client.post(url,{'action':'review','revision':2,'justification':'Test','reference':'Test'}).status_code,403)

    def test_expired_tampered_wrong_user_and_wrong_operation_tokens(self):
        token=self.preview()
        self.assertEqual(self.apply(token+'tampered').status_code,409)
        with patch('django.core.signing.time.time',return_value=9999999999):
            self.assertEqual(self.apply(token).status_code,409)
        other=get_user_model().objects.create_superuser('synthetic-second',password='synthetic')
        self.client.force_login(other)
        self.assertEqual(self.apply(token).status_code,409)
        self.client.force_login(self.user)
        self.assertEqual(self.apply(token).status_code,302)
        event=AuditEvent.objects.get(action=service.ACTION)
        self.assertEqual(self.client.post(reverse('course_correction_reverse',args=[self.excel.pk,event.pk]),
            {'action':'confirm','token':token,'confirmed':'on'}).status_code,409)

    def test_confirmation_required_and_posted_changes_rejected(self):
        token=self.preview()
        self.assertEqual(self.client.post(self.url,{'action':'confirm','token':token}).status_code,400)
        self.assertEqual(self.client.post(self.url,{'action':'confirm','token':token,'confirmed':'on','hours':'7'}).status_code,400)
        self.assertEqual(AuditEvent.objects.count(),34)

    def test_revision_concurrency_and_repeated_confirmation(self):
        token=self.preview()
        Course.objects.filter(pk=self.excel.pk).update(revision=2)
        self.assertEqual(self.apply(token).status_code,409)
        self.assertEqual(AuditEvent.objects.count(),34)
        Course.objects.filter(pk=self.excel.pk).update(revision=1)
        self.assertEqual(self.apply(token).status_code,302)
        self.assertEqual(self.apply(token).status_code,409)
        self.assertEqual(AuditEvent.objects.count(),36)

    def test_any_unversioned_change_also_invalidates_confirmation(self):
        token=self.preview()
        Course.objects.filter(pk=self.excel.pk).update(area='Concurrent change')
        self.assertEqual(self.apply(token).status_code,409)
        self.excel.refresh_from_db();self.assertIsNone(self.excel.default_hours)

    def test_rolls_back_if_individual_audit_fails(self):
        token=self.preview();before=service.course_state(self.excel)
        original=AuditEvent.save
        def fail(event,*args,**kwargs):
            if event.action==service.ACTION:raise RuntimeError('Synthetic audit failure')
            return original(event,*args,**kwargs)
        with patch.object(AuditEvent,'save',fail),self.assertRaises(RuntimeError):self.apply(token)
        self.assertEqual(service.course_state(self.excel),before)
        self.assertEqual(AuditEvent.objects.count(),34)

    def test_rollback_on_conditional_update_failure(self):
        token=self.preview();before=service.course_state(self.excel)
        from django.db.models.query import QuerySet
        original=QuerySet.update
        def reject(qs,**kwargs):return 0 if qs.model is Course else original(qs,**kwargs)
        with patch.object(QuerySet,'update',reject):self.assertEqual(self.apply(token).status_code,409)
        self.assertEqual(service.course_state(self.excel),before)
        self.assertEqual(AuditEvent.objects.count(),34)

    def test_girh_excluded_and_normal_edit_cannot_bypass(self):
        self.assertEqual(self.client.get(reverse('course_correct',args=[self.girh.pk])).status_code,403)
        self.assertEqual(self.client.post(reverse('course_correct',args=[self.girh.pk]),{'action':'review',**self.data()}).status_code,403)
        for course,days in [(self.excel,5),(self.girh,2)]:
            response=self.client.post(reverse('course_edit',args=[course.pk]),{
                'title':course.title,'default_days':days,'default_hours':'6','revision':course.revision,
                'validation_status':'pending','active':'on','duration_reason':'Bypass','duration_reference':'Bypass'})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.context['form'].errors)
        self.excel.refresh_from_db();self.girh.refresh_from_db()
        self.assertIsNone(self.excel.default_hours);self.assertIsNone(self.girh.default_days)
        self.preserved()

    def test_invalid_values_extra_fields_and_documentation(self):
        for changes in [{'hours':'7'},{'days':4},{'hours':'NaN'},{'hours':'Infinity'},
                {'hours':'0'},{'hours':'6.001'},{'days':0},{'days':-1},
                {'justification':' '},{'reference':''},{'correction_type':'typo'},
                {'total_hours':'35'},{'validation_status':'validated'},{'source_metadata':'{}'}]:
            with self.subTest(changes=changes):
                response=self.client.post(self.url,{'action':'review',**self.data(**changes)})
                self.assertEqual(response.status_code,400)
        self.assertEqual(AuditEvent.objects.count(),34)

    def test_service_checks_permissions_even_without_view(self):
        with self.assertRaises(PermissionDenied):service.package(self.excel,self.staff,self.data())
        with self.assertRaises(PermissionDenied):service.package(self.girh,self.user,self.data())

    def test_controlled_reversal_requires_preview_and_new_reason(self):
        self.assertEqual(self.apply(self.preview()).status_code,302)
        original=AuditEvent.objects.get(action=service.ACTION)
        url=reverse('course_correction_reverse',args=[self.excel.pk,original.pk])
        response=self.client.post(url,{'action':'review','revision':2,'justification':'Synthetic reversal','reference':'Synthetic reversal authorisation'})
        self.assertEqual(response.status_code,200)
        token=response.context['confirmation'].initial['token']
        response=self.client.post(url,{'action':'confirm','token':token,'confirmed':'on'})
        self.assertEqual(response.status_code,302)
        self.excel.refresh_from_db();self.assertEqual(self.excel.revision,3);self.assertIsNone(self.excel.default_hours)
        self.assertEqual(AuditEvent.objects.count(),38)
        self.assertEqual(self.client.post(url,{'action':'confirm','token':token,'confirmed':'on'}).status_code,409)
        self.preserved()

    def test_reversal_refuses_later_changes(self):
        self.apply(self.preview());original=AuditEvent.objects.get(action=service.ACTION)
        url=reverse('course_correction_reverse',args=[self.excel.pk,original.pk])
        Course.objects.filter(pk=self.excel.pk).update(validation_notes='Later decision')
        response=self.client.post(url,{'action':'review','revision':2,'justification':'Reason','reference':'Reference'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(AuditEvent.objects.count(),36)

    def test_signed_but_unapproved_values_revalidated_by_service(self):
        token=self.preview();payload=signing.loads(token,salt=service.SALT)
        payload['after']['default_hours']='7.00'
        self.assertEqual(self.apply(signing.dumps(payload,salt=service.SALT)).status_code,409)
        self.assertEqual(AuditEvent.objects.count(),34)

    def test_revoked_permission_blocks_confirmation(self):
        token=self.preview()
        self.user.user_permissions.clear()
        self.assertEqual(self.apply(token).status_code,403)
        self.excel.refresh_from_db();self.assertIsNone(self.excel.default_hours)
        self.assertEqual(AuditEvent.objects.count(),34)

    def test_reversal_token_expires_and_wrong_course_is_denied(self):
        self.apply(self.preview())
        original=AuditEvent.objects.get(action=service.ACTION)
        url=reverse('course_correction_reverse',args=[self.excel.pk,original.pk])
        response=self.client.post(url,{'action':'review','revision':2,'justification':'Reason','reference':'Reference'})
        token=response.context['confirmation'].initial['token']
        with patch('django.core.signing.time.time',return_value=9999999999):
            self.assertEqual(self.client.post(url,{'action':'confirm','token':token,'confirmed':'on'}).status_code,409)
        self.assertEqual(self.client.post(reverse('course_correction_reverse',args=[self.girh.pk,original.pk]),
            {'action':'confirm','token':token,'confirmed':'on'}).status_code,403)


class ConcurrentCorrectionTests(TransactionTestCase):
    def test_only_one_simultaneous_confirmation_can_commit(self):
        user=get_user_model().objects.create_user('synthetic-concurrent',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='change_course',content_type__app_label='training'))
        metadata={'days':'5','hours':'7','title':'Synthetic Excel'}
        course=Course.objects.create(title='Synthetic Excel',source_system=SOURCE_SYSTEM,
            source_action_id='AC-2026-015',source_metadata=metadata,source_digest=service.digest(metadata),
            default_days=5)
        data={'revision':1,'days':5,'hours':Decimal('6'),'correction_type':'documented_standard',
              'justification':'Synthetic concurrent decision','reference':'Synthetic reference'}
        _,token=service.package(course,user,data)
        def run():
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=user.pk)
                target=Course.objects.get(pk=course.pk)
                return service.confirm(token,actor,target).pk
            except (ValidationError,DatabaseError):
                return 'rejected'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(results.count('rejected'),1,results)
        self.assertEqual(AuditEvent.objects.filter(action=service.ACTION).count(),1)
        self.assertEqual(AuditEvent.objects.filter(action=service.BATCH_ACTION).count(),1)
        course.refresh_from_db()
        self.assertEqual(course.revision,2)
        self.assertEqual(course.total_hours,Decimal('30'))

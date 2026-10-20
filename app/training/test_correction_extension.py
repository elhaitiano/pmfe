"""Four documented standards; all database rows and users are synthetic."""
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from . import catalogue_corrections as service
from . import test_catalogue_corrections as fixtures
from .models import Course, Training, AuditEvent, TrainingGroup, Enrollment, GroupSession

IDS = ('AC-2026-037','AC-2026-004','AC-2026-005','AC-2026-026')


def prepare_fixtures(cls):
    fixtures.CorrectionTests.setUpTestData.__func__(cls)
    Course.objects.filter(pk=cls.excel.pk).update(default_hours=6, revision=2)
    for identifier in IDS:
        rule=service.RULES[identifier]
        course=Course.objects.get(source_action_id=identifier)
        metadata={'title':course.title,'days':str(rule['days']),
                  'hours':rule['source_hours'],'origin':'Synthetic official plan'}
        Course.objects.filter(pk=course.pk).update(default_days=rule['days'],default_hours=None,
            source_metadata=metadata,source_digest=service.digest(metadata))
    remaining=list(Training.objects.filter(title='Synthetic other history').order_by('pk').values_list('pk',flat=True))
    position=0
    for title,count,days,hours in [('MBMB',16,2,7),('COP1',25,7,6),('COP2',21,7,6),('GRSE',76,1,6)]:
        Training.objects.filter(pk__in=remaining[position:position+count]).update(title='Synthetic '+title,days=days,hours_per_day=hours)
        position+=count
    AuditEvent.objects.bulk_create([AuditEvent(actor='Synthetic Excel reviewer',action='PREVIOUS_EXCEL_FIXTURE',detail=str(i)) for i in range(2)])


class ExtensionTests(TestCase):
    @classmethod
    def setUpTestData(cls):prepare_fixtures(cls)

    def setUp(self):
        self.client.force_login(self.user)
        self.baseline={r['id']:r for r in service.canonical(list(Course.objects.order_by('pk').values()))}
        self.history=service.digest(list(Training.objects.order_by('pk').values()))
        self.audits=list(AuditEvent.objects.order_by('pk').values())

    def course(self,identifier):return Course.objects.get(source_action_id=identifier)

    def url(self,course):return reverse('course_correct',args=[course.pk])

    def data(self,course,**extra):
        rule=service.RULES[course.source_action_id]
        return {'action':'review','revision':course.revision,'days':rule['days'],'hours':rule['hours'],
                'correction_type':'documented_standard','justification':'Synthetic administrative standard decision',
                'reference':'Synthetic documentary reference',**extra}

    def review(self,course):
        response=self.client.post(self.url(course),self.data(course))
        self.assertEqual(response.status_code,200,response.content[:400])
        return response.context['confirmation'].initial['token']

    def confirm(self,course,token):
        return self.client.post(self.url(course),{'action':'confirm','token':token,'confirmed':'on'})

    def preserved(self):
        self.assertEqual(Training.objects.count(),2057)
        self.assertEqual(service.digest(list(Training.objects.order_by('pk').values())),self.history)
        for old in self.audits:self.assertEqual(AuditEvent.objects.values().get(pk=old['id']),old)
        for model in (TrainingGroup,Enrollment,GroupSession):self.assertFalse(model.objects.exists())

    def test_four_previews_have_specific_proposals_and_warnings_without_writes(self):
        texts={'AC-2026-037':'três dias','AC-2026-004':'COP1','AC-2026-005':'COP2','AC-2026-026':'76 participações'}
        for identifier in IDS:
            course=self.course(identifier);rule=service.RULES[identifier]
            page=self.client.get(self.url(course));self.assertEqual(page.status_code,200)
            self.assertContains(page,texts[identifier])
            self.assertNotContains(page,'17 participações históricas')
            if identifier in ('AC-2026-004','AC-2026-005'):
                self.assertContains(page,'42 horas')
                self.assertContains(page,'não está comprovada')
            response=self.client.post(self.url(course),self.data(course))
            self.assertEqual(response.status_code,200)
            self.assertEqual(Decimal(response.context['preview']['total_after']),Decimal(rule['days'])*Decimal(rule['hours']))
        self.assertEqual(AuditEvent.objects.count(),36)
        self.assertEqual(service.canonical(list(Course.objects.order_by('pk').values())),list(self.baseline.values()))

    def test_four_confirmations_are_exact_audited_and_preserve_all_other_rows(self):
        targets=set()
        for identifier in IDS:
            course=self.course(identifier);targets.add(course.pk)
            self.assertEqual(self.confirm(course,self.review(course)).status_code,302)
            course.refresh_from_db();rule=service.RULES[identifier]
            self.assertEqual(course.total_hours,Decimal(rule['days'])*Decimal(rule['hours']))
            self.assertEqual(course.revision,2)
            current=service.course_state(course);old=self.baseline[course.pk]
            for key in old.keys()-{'default_days','default_hours','revision'}:self.assertEqual(current[key],old[key])
            event=AuditEvent.objects.filter(action=service.ACTION).latest('pk')
            detail=json.loads(event.detail)
            self.assertEqual(detail['evidence'],rule['evidence'])
            self.assertEqual(detail['source_action_id'],identifier)
        self.assertEqual(AuditEvent.objects.count(),44)
        for row in service.canonical(list(Course.objects.order_by('pk').values())):
            if row['id'] not in targets:self.assertEqual(row,self.baseline[row['id']])
        self.preserved()

    def test_permission_csrf_and_direct_post_protection_for_every_action(self):
        for identifier in IDS:
            course=self.course(identifier)
            anonymous=Client();self.assertEqual(anonymous.get(self.url(course)).status_code,302)
            unauthorised=Client();unauthorised.force_login(self.staff)
            self.assertEqual(unauthorised.get(self.url(course)).status_code,403)
            self.assertEqual(unauthorised.post(self.url(course),self.data(course)).status_code,403)
            csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
            self.assertEqual(csrf.post(self.url(course),self.data(course)).status_code,403)
            self.assertEqual(self.client.post(self.url(course),self.data(course,hours='8')).status_code,400)
        self.assertEqual(AuditEvent.objects.count(),36)

    def test_signed_tokens_cannot_be_moved_between_actions_or_tampered(self):
        first,second=self.course(IDS[0]),self.course(IDS[1])
        token=self.review(first)
        self.assertEqual(self.confirm(second,token).status_code,409)
        self.assertEqual(self.confirm(first,token+'invalid').status_code,409)
        with patch('django.core.signing.time.time',return_value=9999999999):
            self.assertEqual(self.confirm(first,token).status_code,409)
        self.assertEqual(AuditEvent.objects.count(),36)

    def test_stale_revisions_replay_and_normal_edit_bypass_for_each(self):
        for identifier in IDS:
            course=self.course(identifier);token=self.review(course)
            Course.objects.filter(pk=course.pk).update(revision=2)
            self.assertEqual(self.confirm(course,token).status_code,409)
            Course.objects.filter(pk=course.pk).update(revision=1)
            rule=service.RULES[identifier]
            response=self.client.post(reverse('course_edit',args=[course.pk]),{
                'title':course.title,'default_days':rule['days'],'default_hours':rule['hours'],
                'revision':1,'validation_status':'pending','active':'on',
                'duration_reason':'Bypass','duration_reference':'Bypass'})
            self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
            self.assertEqual(self.confirm(course,token).status_code,302)
            self.assertEqual(self.confirm(course,token).status_code,409)
        self.preserved()

    def test_audit_failure_rolls_back_each_correction(self):
        for identifier in IDS:
            course=self.course(identifier);token=self.review(course);before=service.course_state(course)
            original=AuditEvent.save
            def fail(event,*args,**kwargs):
                if event.action==service.ACTION:raise RuntimeError('Synthetic audit failure')
                return original(event,*args,**kwargs)
            with patch.object(AuditEvent,'save',fail),self.assertRaises(RuntimeError):self.confirm(course,token)
            self.assertEqual(service.course_state(course),before)
            self.assertEqual(AuditEvent.objects.count(),36)

    def test_each_correction_can_be_reversed_with_a_new_signed_confirmation(self):
        for identifier in IDS:
            course=self.course(identifier)
            self.confirm(course,self.review(course))
            event=AuditEvent.objects.filter(action=service.ACTION).latest('pk')
            url=reverse('course_correction_reverse',args=[course.pk,event.pk])
            response=self.client.post(url,{'action':'review','revision':2,
                'justification':'Synthetic reversal decision','reference':'Synthetic reversal reference'})
            self.assertEqual(response.status_code,200)
            token=response.context['confirmation'].initial['token']
            self.assertEqual(self.client.post(url,{'action':'confirm','token':token,'confirmed':'on'}).status_code,302)
            course.refresh_from_db();self.assertIsNone(course.default_hours);self.assertEqual(course.revision,3)
            self.assertEqual(self.client.post(url,{'action':'confirm','token':token,'confirmed':'on'}).status_code,409)
        self.preserved()

    def test_reversals_refuse_later_decisions(self):
        for identifier in IDS:
            course=self.course(identifier);self.confirm(course,self.review(course))
            event=AuditEvent.objects.filter(action=service.ACTION).latest('pk')
            Course.objects.filter(pk=course.pk).update(validation_notes='Synthetic subsequent decision')
            response=self.client.post(reverse('course_correction_reverse',args=[course.pk,event.pk]),
                {'action':'review','revision':2,'justification':'Reversal','reference':'Reference'})
            self.assertEqual(response.status_code,400)

    def test_excel_revision_two_girh_and_other_actions_remain_excluded_from_new_changes(self):
        excel=self.course('AC-2026-015')
        self.assertEqual(excel.revision,2)
        self.assertEqual(self.client.post(self.url(excel),self.data(excel)).status_code,400)
        for identifier in ('AC-2026-003','AC-2026-001','AC-2026-006','HC-RDSC'):
            course=self.course(identifier)
            self.assertEqual(self.client.get(self.url(course)).status_code,403)
            self.assertEqual(self.client.post(self.url(course),{'action':'review',**self.data(excel)}).status_code,403)
        self.assertEqual(AuditEvent.objects.count(),36)
        self.preserved()

    def test_source_drift_or_digest_change_blocks_all_four(self):
        for identifier in IDS:
            course=self.course(identifier);old=course.source_digest
            Course.objects.filter(pk=course.pk).update(source_digest='unexpected')
            self.assertEqual(self.client.post(self.url(course),self.data(course)).status_code,400)
            Course.objects.filter(pk=course.pk).update(source_digest=old)
        self.assertEqual(AuditEvent.objects.count(),36)


class ConcurrentExtensionTests(TransactionTestCase):
    def test_each_decision_only_commits_once_under_concurrent_confirmation(self):
        # Use the same permission/service path, with fresh independent synthetic actions.
        from django.contrib.auth.models import Permission
        user=get_user_model().objects.create_user('synthetic-extension-concurrency',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='change_course',content_type__app_label='training'))
        for identifier in IDS:
            rule=service.RULES[identifier]
            meta={'days':str(rule['days']),'hours':rule['source_hours']}
            course=Course.objects.create(title='Synthetic '+identifier,source_system='epal-proagua-catalogo',
                source_action_id=identifier,default_days=rule['days'],source_metadata=meta,source_digest=service.digest(meta))
            _,token=service.package(course,user,{'revision':1,'days':rule['days'],'hours':Decimal(rule['hours']),
                'correction_type':'documented_standard','justification':'Synthetic decision','reference':'Synthetic reference'})
            def run():
                close_old_connections()
                try:
                    actor=get_user_model().objects.get(pk=user.pk)
                    target=Course.objects.get(pk=course.pk)
                    return service.confirm(token,actor,target).pk
                except (ValidationError,DatabaseError):return 'rejected'
                finally:close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
            self.assertEqual(results.count('rejected'),1,results)
            course.refresh_from_db();self.assertEqual(course.revision,2)
        self.assertEqual(AuditEvent.objects.filter(action=service.ACTION).count(),4)
        self.assertEqual(AuditEvent.objects.filter(action=service.BATCH_ACTION).count(),4)

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from unittest.mock import patch
from .models import Course,ConsolidatedEdition,ConsolidatedParticipation,Employee,HistoricalInterfaceLink,Training,AuditEvent
from .catalogue_import import digest
from .course_codes import attach as attach_codes
from . import documentary_readiness as readiness,group_validation as service
from .test_group_validation import GroupValidationTests as Fixtures

class CourseCodeTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('synthetic-code-manager',password=None)
        self.client.force_login(self.user)
    def course(self,code='COP1',identifier='SYN-ONE',title='Synthetic official title'):
        meta={'id':identifier,'title':title,'code':code,'origin':'Synthetic documentary reference'}
        return Course.objects.create(title=title,source_system='epal-proagua-catalogo',source_action_id=identifier,source_internal_code=code,source_metadata=meta,source_digest=digest(meta))
    def proof(self,c,code='COP1',active=True):
        return ConsolidatedEdition.objects.create(source_key='synthetic-'+str(ConsolidatedEdition.objects.count()),course=c,course_reference=code,title=c.title,group_label='1',active=active,evidence={'code':code,'sources':[{'document':'a'*64,'source':'Synthetic evidence.xlsx','preparatory_or_derived_or_synthetic_source':False}]})
    def profile(self,c):return attach_codes([c])[0].code_profile
    def test_source_code_without_corroboration_remains_pending(self):
        c=self.course();self.assertEqual(self.profile(c)['label'],'Código por confirmar')
    def test_exact_catalogue_and_history_evidence_confirms_documentary_code(self):
        c=self.course();self.proof(c)
        p=self.profile(c);self.assertEqual(p['label'],'COP1');self.assertFalse(p['formal_course_approval'])
        c.refresh_from_db();self.assertEqual(c.code,'');self.assertEqual(c.internal_code,'')
    def test_code2_requires_its_own_exact_evidence(self):
        c=self.course('COP2');self.proof(c,'COP1');self.assertFalse(self.profile(c)['confirmed'])
        self.proof(c,'COP2');self.assertEqual(self.profile(c)['code'],'COP2')
    def test_tampered_source_digest_not_accepted(self):
        c=self.course();self.proof(c);c.source_digest='0'*64;c.save();self.assertFalse(self.profile(c)['confirmed'])
    def test_changed_title_requires_review(self):
        c=self.course();self.proof(c);c.title='Changed title';c.save();self.assertFalse(self.profile(c)['confirmed'])
    def test_historical_title_similarity_does_not_assign_code(self):
        original=self.course();self.proof(original)
        c=Course.objects.create(title='Synthetic official title (historical)',source_system='epal-historical-evidence',source_action_id='HIST',source_internal_code='COP1')
        self.assertFalse(self.profile(c)['confirmed'])
    def test_existing_approved_code_preserves_documented_alias(self):
        c=self.course('WSFR');c.code='WSFT';c.internal_code='WSFT';c.save()
        p=self.profile(c);self.assertEqual(p['code'],'WSFT');self.assertEqual(p['source_candidate'],'WSFR')
    def test_conflicting_stored_fields_are_not_guessed(self):
        c=self.course();c.code='ONE';c.internal_code='TWO';c.save();self.assertTrue(self.profile(c)['conflict'])
        self.assertEqual(self.profile(c)['label'],'Código por confirmar')
    def test_duplicate_code_across_catalogue_identities_is_review(self):
        first=self.course();second=self.course(identifier='SYN-TWO',title='Other action')
        self.proof(first);self.proof(second)
        for c in [first,second]:self.assertTrue(self.profile(c)['conflict']);self.assertFalse(self.profile(c)['confirmed'])
    def test_same_code_reused_in_editions_is_not_a_conflict(self):
        c=self.course();self.proof(c);self.proof(c,active=False)
        self.assertEqual(self.profile(c)['code'],'COP1');self.assertFalse(self.profile(c)['conflict'])
    def test_preparatory_only_evidence_does_not_confirm_code(self):
        c=self.course();e=self.proof(c);e.evidence['sources'][0]['preparatory_or_derived_or_synthetic_source']=True;e.save()
        self.assertFalse(self.profile(c)['confirmed'])
    def test_code_badge_long_title_four_buttons_and_no_calendar(self):
        c=self.course(title='Synthetic official long title '+('com formação ' * 25));self.proof(c)
        r=self.client.get(reverse('planning'));self.assertContains(r,'plan-code-badge');self.assertContains(r,'COP1');self.assertContains(r,c.title)
        for label in ['Ver grupos (1)','Criar G2','Editar acção','Eliminar formação','Ver detalhes']:self.assertContains(r,label)
        self.assertNotContains(r,'id="grupos"')
    def test_unknown_code_has_existing_regularization_link_for_staff(self):
        c=self.course();r=self.client.get(reverse('planning'))
        self.assertContains(r,'Código por confirmar');self.assertContains(r,'Regularizar código')
    def test_reader_cannot_regularize_or_post_to_course_edit(self):
        c=self.course();reader=get_user_model().objects.create_user('synthetic-code-reader');self.client.force_login(reader)
        self.assertNotContains(self.client.get(reverse('planning')),'Regularizar código')
        self.assertEqual(self.client.post(reverse('course_edit',args=[c.pk]),{'code':'FAKE'}).status_code,403)
    def test_code_presentation_is_read_only(self):
        c=self.course();self.proof(c);before=list(Course.objects.values());events=AuditEvent.objects.count()
        self.client.get(reverse('planning'));self.assertEqual(list(Course.objects.values()),before);self.assertEqual(AuditEvent.objects.count(),events)
    def test_missing_source_list_remains_pending_without_exception(self):
        c=self.course();e=self.proof(c);e.evidence={'code':'COP1','sources':None};e.save()
        self.assertFalse(self.profile(c)['confirmed']);self.assertEqual(self.client.get(reverse('planning')).status_code,200)

class DocumentaryReadinessTests(TestCase):
    setUp=Fixtures.setUp
    member=Fixtures.member
    rule=Fixtures.rule
    def group(self):
        self.edition.historical_edition=True;return self.edition
    def summary(self):
        g=self.group();readiness.attach([g],self.user);return g.documentary_readiness
    def test_ready_administratively_without_numeric_grade(self):
        self.member(grade=False,complete=False);r=self.summary()
        self.assertEqual(r['category'],'A');self.assertEqual(r['eligible'],1);self.assertFalse(r['academic_approval_generated'])
    def test_partial_group_does_not_require_all_certified(self):
        self.member(1);self.member(2,delivered=False,sources=False,complete=False)
        r=self.summary();self.assertEqual(r['category'],'B');self.assertEqual(r['eligible'],1);self.assertEqual(r['pending'],1)
    def test_no_certificate_needed_when_existing_completion_evidence_suffices(self):
        self.member(delivered=False);self.assertEqual(self.summary()['category'],'A')
    def test_conflict_requires_review(self):
        self.member(issues=[{'field':'attendance'}]);r=self.summary();self.assertEqual(r['category'],'C');self.assertTrue(r['inconsistent'])
    def test_unlinked_participant_is_pending(self):
        person=Employee.objects.create(number='SYN-UNLINKED',name='Synthetic unlinked participant')
        ConsolidatedParticipation.objects.create(employee=person,edition=self.edition,source_key='SYN-UNLINKED')
        r=self.summary();self.assertEqual(r['category'],'C');self.assertEqual(r['pending'],1)
        self.assertIn('sem ligação operacional',r['reasons'][0][0])
    def test_empty_group_requires_review(self):
        r=self.summary();self.assertEqual(r['category'],'C');self.assertTrue(r['reasons'])
    def test_existing_decision_preserved_and_stale_fingerprint_reviewed(self):
        t,p=self.member();review=service.preview(self.user,[self.key]);service.apply(self.user,review['token'],True)
        self.assertEqual(self.summary()['protected'],1)
        Training.objects.filter(pk=t.pk).update(observations='Synthetic changed evidence')
        r=self.summary();self.assertEqual(r['category'],'C');self.assertEqual(r['review_existing'],1)
    def test_summary_matches_existing_engine(self):
        self.member(1);self.member(2,issues=['Synthetic conflict']);self.member(3,delivered=False,sources=False,complete=False)
        expected=readiness.summarize(service.collect(self.key));actual=self.summary()
        for k in ['category','eligible','pending','protected','participants','completed','inconsistent','reasons']:self.assertEqual(actual[k],expected[k],k)
    def test_summary_no_token_no_business_writes(self):
        t,p=self.member();before=service.values(t);events=AuditEvent.objects.count();r=self.summary()
        t.refresh_from_db();self.assertEqual(service.values(t),before);self.assertEqual(AuditEvent.objects.count(),events);self.assertNotIn('token',r)
    def test_authorization_required_for_summary(self):
        self.member()
        with self.assertRaises(PermissionDenied):readiness.attach([self.group()],self.reader)
    def test_unauthorized_group_page_has_no_documentary_details(self):
        self.member();self.client.force_login(self.reader)
        r=self.client.get(reverse('course_groups',args=[self.course.pk]));self.assertEqual(r.status_code,200)
        self.assertNotContains(r,'Preparação documental');self.assertNotContains(r,'Consultar preparação e pendências')
    def test_authorized_page_has_existing_preview_and_correction_links(self):
        t,p=self.member(delivered=False,complete=False,sources=False)
        r=self.client.get(reverse('course_groups',args=[self.course.pk]))
        self.assertContains(r,'Preparação documental');self.assertContains(r,'C — Necessita de revisão');self.assertContains(r,reverse('training_edit',args=[t.pk]))
        self.assertContains(r,reverse('group_validation')+'?group=edition:'+str(self.edition.pk))
    def test_no_change_permission_gets_consultation_link(self):
        t,p=self.member(delivered=False,complete=False,sources=False)
        self.user.is_staff=False;self.user.save()
        g=self.group();readiness.attach([g],self.user)
        self.assertEqual(g.documentary_readiness['pending_rows'][0]['documentary_url'],reverse('consolidated_participation',args=[p.pk]))
    def test_optimized_engine_keeps_original_source_data(self):
        t,p=self.member();original=service.collect(self.key)['rows'][0];summary=readiness.edition_data([self.group()])[self.key]['rows'][0]
        for k,v in original.items():
            if k not in ['fingerprint','source_data']:self.assertEqual(v,summary[k],k)
        self.assertIsNone(summary['source_data']);t.refresh_from_db();self.assertEqual(t.source_data['preserved'],'original')
    def test_summary_uses_bounded_query_count(self):
        for n in range(1,6):self.member(n)
        with self.assertNumQueries(7):readiness.edition_data([self.group()])

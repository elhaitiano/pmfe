"""Final presentation and daily archive tests, synthetic database only."""
import io,json
from zipfile import ZipFile
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.urls import reverse
from . import test_planning_workflow as fixtures
from . import draft_planning as service
from .models import AuditEvent,Training,TrainingGroup

@override_settings(PMF_SIMPLIFIED_PLANNING_ENABLED=True,PMF_HISTORICAL_ENABLED=False)
class AttendanceFinalTests(TestCase):
    setUp=fixtures.PlanningWorkflowTests.setUp
    draft=fixtures.PlanningWorkflowTests.draft
    member=fixtures.PlanningWorkflowTests.member
    publish=fixtures.PlanningWorkflowTests.publish
    prior=fixtures.PlanningWorkflowTests.prior

    def test_all_days_zip_keeps_each_institutional_workbook(self):
        from openpyxl import load_workbook
        draft=self.draft();self.member(draft)
        count=AuditEvent.objects.count()
        with patch('training.draft_planning.preparation_excel',wraps=service.preparation_excel) as renderer:
            response=self.client.get(reverse('planning_draft_excel',args=[draft.pk]),{'all':'1'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(renderer.call_count,2)
        with ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            self.assertEqual(archive.namelist(),['PREPARACAO-RASCUNHO-2026-01-05.xlsx','PREPARACAO-RASCUNHO-2026-01-07.xlsx'])
            for name in archive.namelist():
                content=archive.read(name)
                with ZipFile(io.BytesIO(content)) as book:
                    self.assertTrue(any(x.startswith('xl/media/') for x in book.namelist()))
                workbook=load_workbook(io.BytesIO(content))
                text=' '.join(str(c.value or '') for row in workbook.active for c in row)
                self.assertIn('RASCUNHO',text);self.assertIn(self.people[0].name,text)
                workbook.close()
        self.assertEqual(AuditEvent.objects.count(),count)
        self.assertEqual(TrainingGroup.objects.count(),0)

    def test_blocked_export_identifies_person_and_authorised_review(self):
        draft=self.draft();self.member(draft);self.prior()
        for query in ({'all':'1'},{'date':'2026-01-05'}):
            response=self.client.get(reverse('planning_draft_excel',args=[draft.pk]),query)
            self.assertContains(response,self.people[0].name,status_code=400)
            self.assertContains(response,self.people[0].number,status_code=400)
            self.assertContains(response,'Rever participação',status_code=400)
        self.assertEqual(draft.participants.count(),1)

    def test_archive_failure_never_returns_partial_download(self):
        from django.core.exceptions import ValidationError
        draft=self.draft();self.member(draft)
        with patch('training.draft_planning.preparation_excel',side_effect=[b'first',ValidationError('Second day invalid')]):
            response=self.client.get(reverse('planning_draft_excel',args=[draft.pk]),{'all':'1'})
        self.assertEqual(response.status_code,400)
        self.assertNotIn('Content-Disposition',response)

    def test_form_dates_approval_and_hidden_technical_history(self):
        draft=self.draft();self.member(draft)
        self.course.validation_status='pending';self.course.save()
        response=self.client.get(reverse('planning_draft_edit',args=[draft.pk]))
        for text in ('2. Formandos do grupo','3. Lista de Presença','4. Aprovação da formação','05/01/2026','07/01/2026','Pendente','Gerar todos os dias'):
            self.assertContains(response,text)
        self.assertNotContains(response,'Histórico da preparação')
        self.assertNotContains(response,'PLANNING_DRAFT_SAVE')
        self.course.refresh_from_db();self.assertEqual(self.course.validation_status,'pending')

    def test_audit_existing_page_human_description_and_unchanged_event(self):
        raw=json.dumps({'draft':'synthetic-id','reason':'Synthetic documented correction','before':[],'after':[1]})
        event=AuditEvent.objects.create(actor='synthetic-admin',action='DRAFT_PARTICIPANTS',detail=raw)
        response=self.client.get(reverse('audit'))
        self.assertContains(response,'Formandos do grupo actualizados')
        self.assertContains(response,'Synthetic documented correction')
        self.assertNotContains(response,'DRAFT_PARTICIPANTS')
        self.assertNotContains(response,'"before"')
        event.refresh_from_db();self.assertEqual(event.detail,raw)
        self.admin.is_staff=False;self.admin.is_superuser=False;self.admin.save()
        self.assertEqual(self.client.get(reverse('audit')).status_code,403)

    def test_published_group_archive_and_permissions(self):
        draft=self.draft();self.member(draft);group=self.publish(draft)
        response=self.client.get(reverse('attendance_excel',args=[group.pk]),{'all':'1'})
        self.assertEqual(response.status_code,200)
        with ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:self.assertEqual(len(archive.namelist()),2)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('planning_draft_excel',args=[draft.pk]),{'all':'1'}).status_code,302)
        self.assertEqual(Training.objects.count(),0)

    def test_operational_export_block_shows_identity_and_review_for_each_mode(self):
        draft=self.draft();self.member(draft);group=self.publish(draft)
        self.prior()
        for query in ({'all':'1'},{'date':'2026-01-05'}):
            response=self.client.get(reverse('attendance_excel',args=[group.pk]),query)
            self.assertContains(response,self.people[0].number,status_code=400)
            self.assertContains(response,'Rever participação',status_code=400)
        self.assertEqual(group.enrollments.count(),1)

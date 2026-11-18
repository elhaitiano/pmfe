"""Attendance preparation changes on disposable synthetic databases only."""
import json
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.urls import reverse
from . import test_planning_workflow as fixtures
from . import draft_planning as service
from .planning import group_deletion_allowed,create_group
from .models import DraftParticipant,AuditEvent,Training,TrainingGroup,Course,Enrollment

@override_settings(PMF_SIMPLIFIED_PLANNING_ENABLED=True,PMF_HISTORICAL_ENABLED=False)
class AttendanceManagementTests(TestCase):
    setUp=fixtures.PlanningWorkflowTests.setUp
    draft=fixtures.PlanningWorkflowTests.draft
    member=fixtures.PlanningWorkflowTests.member
    publish=fixtures.PlanningWorkflowTests.publish
    prior=fixtures.PlanningWorkflowTests.prior
    add=fixtures.PlanningWorkflowTests.add

    def test_draft_correction_records_before_after_reason_and_eligibility(self):
        draft=self.draft();self.member(draft);self.prior()
        form=service.DraftForm({**self.raw,'course':self.course.pk,'revision':draft.revision,'location':'New synthetic room','change_reason':'Corrected preparation room'})
        self.assertTrue(form.is_valid(),form.errors)
        saved=service.save(self.admin,form,draft)
        event=json.loads(AuditEvent.objects.get(action='PLANNING_DRAFT_SAVE').detail)
        self.assertEqual(event['before']['group_data']['location'],self.raw['location'])
        self.assertEqual(event['after']['group_data']['location'],'New synthetic room')
        self.assertEqual(event['reason'],'Corrected preparation room')
        self.assertFalse(event['eligibility'][0]['eligible'])
        response=self.client.get(reverse('planning_draft_excel',args=[saved.pk]),{'date':'2026-01-05'})
        self.assertEqual(response.status_code,400)

    def test_add_remove_preserves_audited_selection_and_reason(self):
        draft=self.draft();url=reverse('planning_draft_people',args=[draft.pk])
        self.client.post(url,{'action':'add','revision':1,'employees':[self.people[0].pk],'reason':'Synthetic list correction'})
        event=json.loads(AuditEvent.objects.get(action='DRAFT_PARTICIPANTS').detail)
        self.assertEqual(event['before'],[]);self.assertEqual(event['after'],[self.people[0].pk])
        self.assertTrue(event['eligibility'][0]['eligible'])
        self.assertEqual(event['reason'],'Synthetic list correction')
        draft.refresh_from_db()
        self.client.post(url,{'action':'remove','revision':draft.revision,'member':draft.participants.get().pk,'reason':'Selected by mistake'})
        self.assertEqual(draft.participants.count(),0)
        self.assertEqual(Training.objects.count(),0)

    def test_management_links_and_history_require_audit_permission(self):
        draft=self.draft()
        response=self.client.get(reverse('planning_draft_edit',args=[draft.pk]))
        self.assertContains(response,'Gerir Lista de Presença')
        self.assertContains(response,'#attendanceUpdatedExcel')
        self.assertNotContains(response,'Histórico da preparação')
        self.assertEqual(Course.objects.count(),1)

    def test_published_group_cannot_be_deleted_even_without_attendance(self):
        draft=self.draft();self.member(draft);group=self.publish(draft)
        self.assertFalse(group_deletion_allowed(group))
        self.client.post(reverse('group_delete',args=[group.pk]),{'revision':group.revision,'confirm':'ELIMINAR','reason':'Attempt to delete published group'})
        self.assertTrue(TrainingGroup.objects.filter(pk=group.pk).exists())
        self.assertEqual(Enrollment.objects.filter(group=group).count(),1)

    def test_group_correction_requires_reason_and_records_exact_changes(self):
        group=create_group(self.course,self.values);url=reverse('group_edit',args=[group.pk])
        values={**self.raw,'revision':group.revision,'location':'Corrected synthetic location'}
        response=self.client.post(url,values)
        self.assertEqual(response.status_code,200)
        group.refresh_from_db();self.assertEqual(group.location,self.raw['location'])
        response=self.client.post(url,{**values,'correction_reason':'Correction authorised for test'})
        self.assertEqual(response.status_code,302)
        group.refresh_from_db();self.assertEqual(group.location,'Corrected synthetic location')
        detail=json.loads(AuditEvent.objects.get(action='PLANNING_GROUP_CORRECTION').detail)
        self.assertEqual(detail['before']['location'],self.raw['location'])
        self.assertEqual(detail['after']['location'],'Corrected synthetic location')

    def test_audit_failure_rolls_back_group_correction_and_calendar(self):
        group=create_group(self.course,self.values)
        with patch('training.planning.record',side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('group_edit',args=[group.pk]),{**self.raw,'revision':group.revision,'location':'Should rollback','correction_reason':'Synthetic rollback test'})
        group.refresh_from_db()
        self.assertEqual(group.location,self.raw['location']);self.assertEqual(group.revision,1)
        self.assertEqual(list(group.sessions.values_list('date',flat=True)),self.values['dates'])

    def test_group_with_real_participation_record_is_protected_from_edit(self):
        group=create_group(self.course,self.values);self.prior(group=group)
        response=self.client.post(reverse('group_edit',args=[group.pk]),{**self.raw,'revision':group.revision,'location':'Forbidden','correction_reason':'Cannot change attendance history'})
        self.assertEqual(response.status_code,302)
        group.refresh_from_db();self.assertEqual(group.location,self.raw['location'])

    def test_new_eligibility_conflict_rolls_back_operational_correction(self):
        group=create_group(self.course,self.values)
        self.add(group,self.people[:1]);group.refresh_from_db()
        self.prior()
        revision=group.revision
        response=self.client.post(reverse('group_edit',args=[group.pk]),{**self.raw,'revision':revision,'location':'Must not persist','correction_reason':'Eligibility recheck test'})
        self.assertEqual(response.status_code,200)
        group.refresh_from_db()
        self.assertEqual(group.revision,revision)
        self.assertEqual(group.location,self.raw['location'])
        self.assertFalse(AuditEvent.objects.filter(action='PLANNING_GROUP_CORRECTION').exists())

    def test_staff_without_specific_group_permission_cannot_correct(self):
        from django.contrib.auth import get_user_model
        user=get_user_model().objects.create_user('synthetic-staff-no-group-permission',password=None,is_staff=True)
        group=create_group(self.course,self.values)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('group_edit',args=[group.pk])).status_code,403)

    def test_regeneration_per_day_keeps_draft_and_signatures_blank(self):
        import io
        from openpyxl import load_workbook
        draft=self.draft();self.member(draft)
        for day in ('2026-01-05','2026-01-07'):
            response=self.client.get(reverse('planning_draft_excel',args=[draft.pk]),{'date':day})
            self.assertEqual(response.status_code,200)
            workbook=load_workbook(io.BytesIO(b''.join(response.streaming_content)))
            sheet=workbook.active
            self.assertIn('RASCUNHO',str(sheet['B6'].value) + ' '.join(str(c.value or '') for row in sheet for c in row))
            workbook.close()
        self.assertEqual(Training.objects.count(),0)
        self.assertEqual(TrainingGroup.objects.count(),0)

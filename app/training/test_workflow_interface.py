"""Synthetic UI/calendar regressions. Never connects to the institutional database."""
from datetime import date
from django.test import TestCase, override_settings
from django.urls import reverse
from . import test_planning_workflow as fixtures
from . import draft_planning as service
from .models import PlanningDraft,DraftParticipant,Training,TrainingGroup

@override_settings(PMF_SIMPLIFIED_PLANNING_ENABLED=True,PMF_HISTORICAL_ENABLED=False)
class WorkflowInterfaceTests(TestCase):
    setUp=fixtures.PlanningWorkflowTests.setUp
    draft=fixtures.PlanningWorkflowTests.draft

    def form(self,**values):
        return service.DraftForm({**self.raw,'revision':0,'course':self.course.pk,**values})

    def test_range_calendar_is_generated_on_server_without_javascript(self):
        form=self.form(calendar_mode='range',start_date='2026-02-27',end_date='2026-03-02',dates='')
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['dates'],[date(2026,2,27),date(2026,2,28),date(2026,3,1),date(2026,3,2)])
        draft=service.save(self.admin,form)
        self.assertEqual(len(draft.group_data['dates'].splitlines()),4)
        self.assertEqual(TrainingGroup.objects.count(),0)

    def test_invalid_ranges_and_hours_keep_server_validation(self):
        for start,end in [('2026-01-05','2026-01-04'),('2026-02-30','2026-03-01'),('2026-01-01','2027-01-02'),('2026-01-01','')]:
            form=self.form(calendar_mode='range',start_date=start,end_date=end)
            self.assertFalse(form.is_valid(),(start,end))
        form=self.form(calendar_mode='range',start_date='2026-01-01',end_date='2026-01-02',hours_per_day='20')
        self.assertFalse(form.is_valid())

    def test_custom_calendar_keeps_gaps_and_rejects_duplicates(self):
        draft=self.draft()
        form=service.DraftForm(mode='existing',initial={**draft.group_data,'course':self.course.pk,'revision':1})
        self.assertEqual(form.initial['calendar_mode'],'custom')
        form=self.form(calendar_mode='custom',start_date='2026-01-05',end_date='2026-01-07')
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['dates'],self.values['dates'])
        self.assertFalse(self.form(calendar_mode='custom',dates='2026-01-05\n2026-01-05').is_valid())

    def test_missing_dates_are_still_saved_as_incomplete_draft(self):
        form=self.form(calendar_mode='range',start_date='',end_date='',dates='')
        self.assertTrue(form.is_valid(),form.errors)
        draft=service.save(self.admin,form)
        self.assertEqual(draft.group_data['dates'],'')

    def test_selection_table_labels_saved_state_and_confirmation(self):
        draft=self.draft();url=reverse('planning_draft_people',args=[draft.pk])
        response=self.client.get(url)
        self.assertContains(response,'draftSelectedCount')
        self.assertContains(response,'planning-workflow.js')
        self.assertContains(response,f'for="candidate-{self.people[0].pk}"')
        response=self.client.post(url,{'action':'add','revision':draft.revision,'employees':[self.people[0].pk,self.people[1].pk]},follow=True)
        self.assertContains(response,'Formandos adicionados')
        self.assertContains(response,'checked disabled')
        self.assertEqual(draft.participants.count(),2)
        draft.refresh_from_db()
        response=self.client.post(url,{'action':'remove','revision':draft.revision,'member':draft.participants.first().pk},follow=True)
        self.assertEqual(response.status_code,200)
        self.assertEqual(draft.participants.count(),1)
        self.assertEqual(Training.objects.count(),0)

    def test_search_pagination_and_export_keep_saved_participants(self):
        draft=self.draft()
        DraftParticipant.objects.create(draft=draft,employee=self.people[0])
        url=reverse('planning_draft_people',args=[draft.pk])
        response=self.client.get(url,{'q':'not-found','page':2})
        self.assertContains(response,'Nenhum formando encontrado')
        self.assertEqual(draft.participants.count(),1)
        response=self.client.get(reverse('planning_draft_excel',args=[draft.pk]),{'date':'2026-01-05'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'PK'))
        self.assertEqual(PlanningDraft.objects.get(pk=draft.pk).state,'draft')

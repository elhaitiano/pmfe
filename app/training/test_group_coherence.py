import json
from datetime import date
from django.urls import reverse
from .test_group_counts import GroupCountTests
from .planning import group_participant_counts, group_deletion_allowed
from .group_roster import summaries, attendance_rows
from .batch import edition_queryset
from .analytics import statistics


class GroupCoherenceTests(GroupCountTests):
    def test_historical_detail_matches_index_without_false_empty_or_restart(self):
        t = self.training(self.people[0], 'pilar3b:HIST')
        t.status = 'Concluído'; t.save()
        self.bind(['HIST'])
        index = self.client.get(reverse('course_groups',args=[self.group.course_id]))
        detail = self.client.get(reverse('group_detail', args=[self.group.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(index.context['page'].object_list[0].participant_count, detail.context['participant_count'])
        self.assertContains(detail, 'Registo histórico')
        self.assertNotContains(detail, '0 / 30')
        self.assertNotContains(detail, 'Adicione os formandos para preencher')
        self.assertNotContains(detail, 'Iniciar grupo')
        self.assertEqual(len(detail.context['roster']['members']), 1)
        self.assertEqual(detail.context['roster']['verified_present_count'], 0)
        self.assertFalse(group_deletion_allowed(self.group))

    def test_roster_default_exports_and_batch_share_bound_group(self):
        one = self.training(self.people[0], 'pilar3b:ONE')
        two = self.training(self.people[1], 'pilar3b:TWO')
        # Explicit identity remains valid even when source period differs.
        two.end = date(2026,1,4); two.save()
        self.bind(['ONE','TWO'])
        self.assertEqual(len(attendance_rows(self.group)), 2)
        self.assertEqual(set(edition_queryset(one).values_list('pk',flat=True)), {one.pk,two.pk})
        response = self.client.get(reverse('attendance_pdf', args=[self.group.pk]), {'date':'2026-01-01'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
        batch = self.client.get(reverse('batch_edit', args=[one.pk]))
        self.assertEqual(batch.context['formset'].total_form_count(), 2)

    def test_distinct_roster_and_metrics_separate_people_records_and_presence(self):
        first = self.training(self.people[0], 'pilar3b:A')
        second = self.training(self.people[0], 'pilar3b:B')
        self.bind(['A','B'])
        summary = summaries([self.group])[self.group.pk]
        self.assertEqual(summary['participant_count'], 1)
        self.assertEqual(summary['participation_count'], 2)
        self.assertEqual(summary['verified_present_count'], 0)
        stats = statistics([first,second])
        self.assertEqual(stats['registered_people'], 1)
        self.assertEqual(stats['participations'], 2)
        self.assertEqual(group_participant_counts([self.group])[self.group.pk], 1)

import json
from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Employee, Training, Enrollment, AuditEvent, Course
from .planning import create_group, group_participant_counts, historical_group_trainings
from . import test_planning


class GroupCountTests(TestCase):
    def setUp(self):
        self.fixture = test_planning.PlanningTests('test_calendar_checks_duplicate_days_and_hours')
        self.fixture.client = self.client
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.group = self.fixture.group
        self.people = self.fixture.people
        self.client.force_login(self.fixture.staff)

    def training(self, person, key, group=None):
        return Training.objects.create(employee=person, title='Teste', start=date(2026,1,1), end=date(2026,1,3), days=2, hours_per_day=7, attendance=0, attendance_confirmed=False, certified=None, group=group, source_key=key)

    def bind(self, keys):
        AuditEvent.objects.create(action='IMPORT_GROUPS_70_V1', detail=json.dumps({'group_id':self.group.pk,'source':{'IDs de participação':' | '.join(keys)}}))

    def test_historical_people_without_certificate_or_verified_attendance(self):
        self.training(self.people[0], 'pilar3b:P1')
        self.training(self.people[1], 'pilar3b:P2')
        self.bind(['P1', 'P2'])
        self.assertEqual(group_participant_counts([self.group])[self.group.pk], 2)
        self.assertEqual(historical_group_trainings(self.group).count(), 2)
        response = self.client.get(reverse('course_groups',args=[self.group.course_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'].object_list[0].participant_count, 2)
        self.assertNotContains(response, '0 / 30')
        self.assertContains(response, 'Formandos registados')

    def test_union_deduplicates_and_excludes_cancelled_only_enrolment(self):
        self.fixture.add(self.group, self.people[:1])
        self.training(self.people[0], 'pilar3b:P1', self.group)
        self.training(self.people[0], 'pilar3b:P2')
        self.training(self.people[1], 'pilar3b:P3', self.group)
        self.bind(['P1','P2','P3'])
        Enrollment.objects.create(group=self.group, employee=self.people[2], state='Cancelada')
        self.assertEqual(group_participant_counts([self.group])[self.group.pk], 2)

    def test_unbound_similar_training_does_not_count_and_reads_do_not_write(self):
        self.training(self.people[0], 'pilar3b:UNBOUND')
        before = (Training.objects.count(), Enrollment.objects.count(), AuditEvent.objects.count())
        self.assertEqual(group_participant_counts([self.group])[self.group.pk], 0)
        self.assertEqual(group_participant_counts([]), {})
        self.assertEqual((Training.objects.count(), Enrollment.objects.count(), AuditEvent.objects.count()), before)

from datetime import date, time
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from .models import Course, TrainingGroup, GroupSession, SessionAttendance, Employee, Training, GroupLogisticsEvaluation, TrainingEvaluation
from .outcomes import outcomes

def group_fixture(title='Daily'):
    course = Course.objects.create(title=title)
    return TrainingGroup.objects.create(course=course, number=1, title=title, trainer='Trainer', location='Room',
        hours_per_day=7, start_time=time(8), end_time=time(16), start=date(2025,1,30), end=date(2025,2,2),
        days=2, state='Concluída')

def training_fixture(group, number='Daily1'):
    person = Employee.objects.create(number=number, name='Synthetic '+number)
    return Training.objects.create(employee=person, group=group, course=group.course, title=group.title,
        start=group.start, end=group.end, days=2, hours_per_day=7, attendance=0, status='Concluído')

class SessionTests(TestCase):
    def setUp(self):
        self.group=group_fixture()
        self.row=training_fixture(self.group)
        self.session=GroupSession.objects.create(group=self.group,date=date(2025,1,30),duration=6)
    def test_two_sessions_same_day_and_partial_attendance(self):
        other=GroupSession.objects.create(group=self.group,date=self.session.date,code='afternoon',duration=7)
        for session,hours in ((self.session,Decimal('2.5')),(other,Decimal(0))):
            record=SessionAttendance(session=session,training=self.row,hours=hours,confirmed=True,actor='RH')
            record.full_clean();record.save()
        self.row.refresh_from_db()
        self.assertEqual(self.row.attendance,0)
        self.assertFalse(self.row.attendance_confirmed)
        self.assertEqual(self.row.session_attendances.count(),2)
    def test_unknown_not_zero_and_impossible_hours_rejected(self):
        unknown=SessionAttendance(session=self.session,training=self.row,actor='RH')
        unknown.full_clean();unknown.save()
        unknown.confirmed=True
        with self.assertRaises(ValidationError):unknown.full_clean()
        unknown.hours=7
        with self.assertRaises(ValidationError):unknown.full_clean()
    def test_wrong_group_and_duplicates_rejected(self):
        other=training_fixture(group_fixture('Other'),'Other')
        wrong=SessionAttendance(session=self.session,training=other,hours=1,confirmed=True,actor='RH')
        with self.assertRaises(ValidationError):wrong.full_clean()
        SessionAttendance.objects.create(session=self.session,training=self.row,hours=1,confirmed=True,actor='RH')
        with self.assertRaises(IntegrityError), transaction.atomic():
            SessionAttendance.objects.create(session=self.session,training=self.row,hours=1,confirmed=True,actor='RH')
    def test_session_edit_permissions_revision_and_audit(self):
        user=get_user_model().objects.create_user('session-editor',is_staff=True)
        self.client.force_login(user)
        url=reverse('session_edit',args=[self.group.pk,self.session.pk])
        params=dict(date='2025-01-30',code='principal',duration='7',revision=self.group.revision,reason='Agenda reviewed',confirm='on')
        self.assertEqual(self.client.post(url,params).status_code,302)
        self.assertEqual(self.client.post(url,params).status_code,409)
        user.is_staff=False;user.save()
        self.assertEqual(self.client.post(url,params).status_code,403)
    def test_logistics_weighted_groups_and_unknown_weights(self):
        other=group_fixture('Other')
        row=training_fixture(other,'Other')
        for group,score,count in ((self.group,1,2),(other,5,8)):
            GroupLogisticsEvaluation.objects.create(group=group,mean=score,responses=count,
                validation='Validado',date=date(2025,2,2),source='Survey',evaluator='RH')
        result=outcomes([self.row,row])
        self.assertEqual(result['logistics'],Decimal('4.20'))
        self.assertEqual(result['logistics_count'],10)
        TrainingEvaluation.objects.create(training=self.row,logistics=3,validation='Validado',date=date(2025,2,2),source='Individual',evaluator='RH')
        result=outcomes([self.row,row])
        self.assertEqual(result['logistics_count'],9)
        self.assertEqual(result['logistics_excluded_groups'],1)

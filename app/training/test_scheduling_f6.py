from datetime import date, time
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from .models import Course, Trainer, TrainingCentre, TrainingRoom, GroupSession, PlanningLock
from .scheduling import save_group, save_resource, save_trainer

class SchedulingF6Tests(TestCase):
    def setUp(self):
        PlanningLock.objects.get_or_create(pk=1)
        self.course=Course.objects.create(code="TESTE_F6_UNIT",title="TESTE_F6_UNIT")
        self.trainer=Trainer.objects.create(name="TESTE_F6_UNIT_Formador")
        self.centre=save_resource(TrainingCentre(code="TESTE_F6_UNIT",name="TESTE_F6_UNIT"))
        self.room=save_resource(TrainingRoom(centre=self.centre,code="TESTE_F6_UNIT",name="TESTE_F6_UNIT",capacity=20))
    def values(self,**kwargs):
        v=dict(dates=[date(2027,1,1)],start=date(2027,1,1),end=date(2027,1,1),
            trainer=self.trainer,centre=self.centre,room=self.room,planning_confirmation="Confirmado",
            provider="TESTE_F6_UNIT",location="TESTE_F6_UNIT",capacity=10,
            hours_per_day=Decimal("2"),break_hours=Decimal("0"),start_time=time(8),end_time=time(10))
        v.update(kwargs);return v
    def test_confirmed_overlap_blocked(self):
        save_group(self.course.pk,self.values())
        with self.assertRaises(ValidationError):save_group(self.course.pk,self.values())
    def test_adjacent_intervals_allowed(self):
        save_group(self.course.pk,self.values())
        group=save_group(self.course.pk,self.values(start_time=time(10),end_time=time(12)))
        self.assertEqual(group.planning_confirmation,"Confirmado")
    def test_missing_days_stays_provisional(self):
        group=save_group(self.course.pk,self.values(dates=[],planning_confirmation="Provisorio"))
        self.assertEqual(group.sessions.count(),0)
        self.assertEqual(group.days,0)
    def test_weekend_and_selected_holiday_preserved(self):
        dates=[date(2027,1,1),date(2027,1,2),date(2027,1,3)]
        group=save_group(self.course.pk,self.values(dates=dates,end=dates[-1]))
        self.assertEqual(list(group.sessions.order_by("date").values_list("date",flat=True)),dates)
    def test_busy_trainer_cannot_be_disabled(self):
        save_group(self.course.pk,self.values());self.trainer.active=False
        with self.assertRaises(ValidationError):save_trainer(self.trainer)
        self.trainer.refresh_from_db();self.assertTrue(self.trainer.active)
    def test_room_capacity_cannot_invalidate_reservation(self):
        save_group(self.course.pk,self.values());self.room.capacity=5
        with self.assertRaises(ValidationError):save_resource(self.room)
    def test_calendar_edit_keeps_original_session(self):
        group=save_group(self.course.pk,self.values());old=group.sessions.get().pk
        save_group(self.course.pk,self.values(dates=[],planning_confirmation="Provisorio"),group_id=group.pk,revision=group.revision)
        self.assertTrue(GroupSession.all_objects.filter(pk=old,active=False).exists())
    def test_textual_trainer_is_not_automatically_linked(self):
        group=save_group(self.course.pk,self.values(trainer=self.trainer.name,planning_confirmation="Provisorio"))
        self.assertIsNone(group.trainer_record_id)
        self.assertEqual(group.trainer,self.trainer.name)

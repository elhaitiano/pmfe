from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from .models import Employee,Training,TrainingDocumentState
from .analytics import statistics,distribution

class ReconciledIndicatorsTests(TestCase):
    def setUp(self):self.person=Employee.objects.create(number='SYN-PILAR',name='Synthetic source worker')
    def training(self,**kw):
        data=dict(employee=self.person,title='Synthetic training',start=date(2026,1,1),end=date(2026,1,2),days=2,hours_per_day=7,attendance=0,status='Concluído');data.update(kw);return Training.objects.create(**data)
    def test_unknown_zeros_not_absences_or_denominator(self):
        known=self.training(attendance_confirmed=True,attendance=1);unknown=self.training()
        s=statistics([known,unknown]);self.assertEqual(s['attendance'],50);self.assertEqual(s['attendance_days'],2);self.assertEqual(s['hours'],7);self.assertEqual(s['attendance_coverage'],1)
    def test_confirmed_zero_is_a_real_zero(self):
        s=statistics([self.training(attendance_confirmed=True)])
        self.assertEqual(s['attendance'],0);self.assertEqual(s['attendance_days'],2);self.assertEqual(s['hours_value'],0)
    def test_planned_attendance_always_excluded(self):
        s=statistics([self.training(status='Planeado',attendance_confirmed=True)])
        self.assertIsNone(s['attendance']);self.assertEqual(s['attendance_coverage'],0)
    def test_unknown_documentation_is_not_zero(self):
        s=statistics([self.training()]);self.assertIsNone(s['issued_value']);self.assertIsNone(s['learner_delivery_value']);self.assertIsNone(s['hr_delivery_value'])
    def test_emission_is_not_delivery(self):
        t=self.training();TrainingDocumentState.objects.create(training=t,issued=True,reference='Synthetic literal Sim')
        s=statistics([t]);self.assertEqual(s['issued_value'],1);self.assertIsNone(s['learner_delivery_value']);self.assertIsNone(s['hr_delivery_value'])
    def test_explicit_no_separate_from_unknown(self):
        t=self.training();TrainingDocumentState.objects.create(training=t,issued=False)
        s=statistics([t]);self.assertEqual(s['issued_value'],0);self.assertEqual(s['issued_pending'],0)
    def test_delivery_rate_uses_known_values_only(self):
        yes=self.training(certified=True);no=self.training(certified=False);unknown=self.training()
        s=statistics([yes,no,unknown]);self.assertEqual(s['certificates'],1);self.assertEqual(s['learner_delivery_known'],2);self.assertEqual(s['certificates_pending'],1)
    def test_grade_zero_is_real_but_unknown_is_not_zero(self):
        s=statistics([self.training(grade=0),self.training()]);self.assertEqual(s['average'],0);self.assertEqual(s['graded_count'],1)
    def test_source_percentages_do_not_fill_final_grades(self):
        t=self.training()
        with patch('training.source_achievement.achievement',return_value={'percentage':Decimal('75'),'raw':'0.75'}):s=statistics([t])
        self.assertEqual(s['achievement_percent_count'],1);self.assertEqual(s['achievement_percent_average'],75);self.assertIsNone(s['average']);t.refresh_from_db();self.assertIsNone(t.grade)
    def test_duplicates_preserved_but_excluded(self):
        good=self.training(attendance_confirmed=True,attendance=1);duplicate=self.training(source_key='pilar3b:PF-00285',attendance_confirmed=True,attendance=2)
        s=statistics([good,duplicate]);self.assertEqual(s['total'],1);self.assertEqual(s['hours'],7);self.assertEqual(Training.objects.count(),2)
    def test_distribution_excludes_unknown_hours(self):
        rows=distribution([self.training(area='Known',attendance=1,attendance_confirmed=True),self.training(area='Unknown',attendance=2)])
        self.assertEqual(sum(r['hours'] for r in rows),7)

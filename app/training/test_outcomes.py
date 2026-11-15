from datetime import date
from decimal import Decimal
from django.test import TestCase
from .models import Employee, Training, TrainingEvaluation
from .outcomes import outcomes

class OutcomeTests(TestCase):
    def setUp(self):
        self.person = Employee.objects.create(number='OUT', name='Synthetic')
    def row(self, **kwargs):
        values=dict(employee=self.person, title='Outcome', start=date(2025,1,1), end=date(2025,1,2),
                    days=2, hours_per_day=7, attendance=2, attendance_confirmed=True, status='Concluído')
        values.update(kwargs)
        return Training.objects.create(**values)
    def evidence(self, row, **kwargs):
        values=dict(training=row, validation='Validado', date=date(2025,1,2), evaluator='RH', source='Decision',
                    validated_grade=row.grade, validated_result=row.result)
        values.update(kwargs)
        return TrainingEvaluation.objects.create(**values)
    def test_presence_is_not_completion_and_pending_is_not_failure(self):
        a=self.row(result='Aprovado', grade=16)
        b=self.row(result='Reprovado', grade=0, attendance=0)
        self.row()
        self.evidence(a, completion=True)
        self.evidence(b, completion=False)
        result=outcomes(Training.objects.all())
        self.assertEqual(result['completion_rate'],50)
        self.assertEqual(result['completion_pending'],1)
        self.assertEqual(result['approval_rate'],50)
        self.assertEqual(result['learning'],8)
    def test_stale_and_ongoing_results_are_excluded(self):
        a=self.row(result='Aprovado',grade=16)
        self.evidence(a, validated_grade=10)
        b=self.row(status='Em Curso',result='Aprovado',grade=16)
        self.evidence(b, completion=True)
        result=outcomes([a,b])
        self.assertIsNone(result['approval_rate'])
        self.assertIsNone(result['completion_rate'])
    def test_logistics_uses_institutional_scale_without_zero_baseline(self):
        a=self.row();b=self.row()
        self.evidence(a,logistics=1)
        self.evidence(b,logistics=5)
        result=outcomes([a,b])
        self.assertEqual(result['logistics'],3)
        self.assertEqual(result['logistics_percent'],60)
        self.assertEqual(result['logistics_count'],2)
    def test_reference_date_does_not_use_future_decisions(self):
        a=self.row(result='Aprovado',grade=16)
        self.evidence(a,completion=True,date=date(2025,2,1))
        result=outcomes([a],date(2025,1,31))
        self.assertIsNone(result['completion_rate'])
        self.assertIsNone(result['approval_rate'])

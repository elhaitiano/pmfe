from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from .models import Employee,Training
from .dashboard import executive_data

class SourceEvaluationChartTests(TestCase):
    def test_percent_chart_uses_original_scale_and_excludes_unknown_values(self):
        person=Employee.objects.create(number='SYN-CHART',name='Synthetic worker')
        rows=[Training.objects.create(employee=person,title='Synthetic',start=date(2026,1,1),end=date(2026,1,1),days=1,hours_per_day=7,attendance=0) for _ in range(3)]
        def attach(items):
            for row,value in zip(items,[Decimal('0'),Decimal('80'),None]):
                row.source_achievement={'percentage':value}
        with patch('training.source_achievement.attach',side_effect=attach):
            result=executive_data(rows,[person])
        self.assertEqual([b['value'] for b in result['achievement_bars']],[1,0,1,0])
        self.assertEqual(result['final_grades_count'],0)
        self.assertEqual(result['stats']['achievement_percent_count'],2)
        self.assertFalse(Training.objects.exclude(grade=None).exists())

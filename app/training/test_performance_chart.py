from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Employee, Training, AuditEvent


class PerformanceChartTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='CHART', name='Pessoa de teste')
        self.user=get_user_model().objects.create_user('chart-viewer')
        self.client.force_login(self.user)

    def row(self, day, grade):
        return Training.objects.create(employee=self.person,title='Formação '+str(day),start=date(2025,1,day),end=date(2025,1,day),days=1,hours_per_day=7,attendance=0,grade=grade)

    def view(self):
        return self.client.get(reverse('employee_detail',args=[self.person.pk]))

    def test_empty_has_no_invented_line(self):
        self.row(1,None)
        response=self.view()
        self.assertContains(response,'Sem notas registadas.')
        self.assertEqual(response.context['chart_dots'],[])

    def test_single_zero_is_visible_without_invented_evolution(self):
        self.row(1,0)
        response=self.view()
        self.assertEqual(len(response.context['chart_dots']),1)
        self.assertEqual(response.context['chart_dots'][0]['grade'],0)
        self.assertContains(response,'ainda não há comparação')
        self.assertNotContains(response,'<polyline')

    def test_chronological_real_points_and_read_only_details(self):
        self.row(3,18);self.row(1,12);self.row(2,None)
        before=list(Training.objects.values_list('id','grade'))
        events=AuditEvent.objects.count()
        response=self.view()
        dots=response.context['chart_dots']
        self.assertEqual([d['grade'] for d in dots],[12,18])
        self.assertContains(response,'linearGradient')
        self.assertContains(response,'data-period="01/01/2025')
        self.assertEqual(list(Training.objects.values_list('id','grade')),before)
        self.assertEqual(AuditEvent.objects.count(),events)

"""Presentation regressions; no production database or settings are opened."""
from decimal import Decimal
from types import SimpleNamespace
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse


class DashboardVisualTests(SimpleTestCase):
    def test_gauge_retains_actual_percentage_and_unknown_state(self):
        html=render_to_string('executive_gauge.html',{'value':Decimal('73.25'),'label':'Assiduidade','note':'Critério existente'})
        self.assertIn('stroke-dasharray="73.25 100"',html)
        self.assertIn('0%',html)
        self.assertIn('100%',html)
        self.assertIn('Critério existente',html)
        self.assertIn('role="img"',html)
        html=render_to_string('executive_gauge.html',{'value':None,'label':'Assiduidade'})
        self.assertIn('por confirmar',html)
        self.assertNotIn('stroke-dasharray=',html)

    def test_zero_and_full_gauges_are_distinct_from_unknown(self):
        for value in (0,100):
            html=render_to_string('executive_gauge.html',{'value':value,'label':'Conclusão'})
            self.assertIn(f'stroke-dasharray="{value} 100"',html)
            self.assertNotIn('por confirmar',html)

    def test_ranking_preserves_order_values_details_and_profile_link(self):
        people=[]
        for i,average in enumerate((Decimal('17'),Decimal('14')),1):
            people.append({'employee':SimpleNamespace(pk=i,name=f'Pessoa sintética {i}',number=f'TEST-{i}',direction='Direcção de teste',function='Função de teste',category='Categoria de teste'),
                'graded':2,'average':average,'href':f'/dashboard/explorar/?slice=person&value={i}',
                'stats':{'attendance':Decimal('80'),'hours':Decimal('14')}})
        html=render_to_string('executive_excellence.html',{'talents':people})
        self.assertIn('QUADRO DE EXCELÊNCIA',html)
        self.assertIn('CLASSIFICAÇÃO /20',html)
        self.assertLess(html.index('Pessoa sintética 1'),html.index('Pessoa sintética 2'))
        self.assertIn(reverse('employee_data',args=[1]),html)
        self.assertIn('data-drill',html)
        self.assertIn('Função de teste',html)
        self.assertIn('Categoria de teste',html)
        self.assertIn('80,0%',html)
        self.assertIn('scope="col"',html)

    def test_empty_ranking_does_not_invent_results(self):
        html=render_to_string('executive_excellence.html',{'talents':[]})
        self.assertIn('Sem notas finais registadas.',html)
        self.assertNotIn('exec-rank">1',html)

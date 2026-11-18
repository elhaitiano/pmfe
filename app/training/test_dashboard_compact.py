from decimal import Decimal
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class CompactDashboardTests(SimpleTestCase):
    def test_only_requested_panels_are_removed(self):
        cards=[{'label':label,'value':Decimal('50'),'decimal':True,'suffix':'%'}
               for label in ('Técnicos com presença','Assiduidade','Média de desempenho','Horas realizadas comprovadas')]
        html=render_to_string('dashboard.html',{'main_kpis':cards,'secondary_kpis':[{'label':'Sentinela secundária','value':1}],
            'historical_stats':{'groups':3},'completion_rate':Decimal('75'),'stats':{},'has_rows':True,'insights':['Critério existente']})
        self.assertEqual(html.count('class="exec-kpi-card"'),4)
        self.assertIn('Taxa de conclusão',html);self.assertIn('75,0%',html)
        self.assertIn('exec-secondary-kpis',html)
        self.assertIn('Sentinela secundária',html)
        for text in ('Dados comprovados e informação por confirmar','Avaliações — aproveitamento na fonte',
                     'Avaliações qualitativas','Grupos históricos e cobertura documental'):
            self.assertNotIn(text,html)
        for marker in ('id="executiveFilters"','id="execDialog"','id="execTooltip"',
                       'Quadro de excelência','dashboard.js','dashboard.css'):
            self.assertIn(marker,html)

from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from pypdf import PdfReader
from .models import Employee,Training,TrainingEvaluation
from .executive_views import context
from .kpi_catalogue import catalogue,main_cards

class KpiPresentationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('presentation',password='test')
        self.client.force_login(self.user)
        # Every published partition contains at least five people.
        for i in range(10):
            e=Employee.objects.create(number=f'P{i}',name=f'Synthetic {i}',direction='Ops',category='Tech',gender='F')
            row=Training.objects.create(employee=e,title='Course',start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1,attendance_confirmed=True,grade=15,result='Aprovado')
            TrainingEvaluation.objects.create(training=row,validation='Validado',date=date(2025,1,2),evaluator='DRH',source='Synthetic evidence',validated_grade=15,validated_result='Aprovado',completion=True,logistics=4)
    def test_four_cards_order_values_and_twelve_unique_indicators(self):
        response=self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-primary-kpi',count=4)
        self.assertEqual([c['label'] for c in response.context['main_kpis']],['Colaboradores com participação confirmada','Horas frequentadas','Assiduidade','Aproveitamento'])
        self.assertEqual([c['value'] for c in response.context['main_kpis']],[10,70,100,100])
        items=[i for p in response.context['indicator_panels'] for i in p['items']]
        self.assertEqual(sorted(i['number'] for i in items),list(range(1,13)))
        self.assertEqual(len(response.context['indicator_panels']),5)
        for n in range(1,13):self.assertContains(response,f'data-indicator="{n}"',count=1)
        self.assertNotContains(response,'exec-secondary-kpis')
        inactive=next(i for i in items if i['number']==12)
        self.assertEqual(inactive['status'],'Inactivo');self.assertIsNone(inactive['value'])
    def test_same_catalogue_values_in_csv_excel_pdf(self):
        response=self.client.get(reverse('dashboard'),{'export':'csv'})
        self.assertIn('Matriz institucional',response.content.decode())
        response=self.client.get(reverse('participation_executive'),{'export':'xlsx'})
        values=list(load_workbook(BytesIO(response.content))['Critérios e indicadores'].values)
        catalogue_rows=[r for r in values if isinstance(r[0],int)]
        self.assertEqual([r[0] for r in catalogue_rows],list(range(1,13)))
        self.assertEqual(catalogue_rows[0][2],70)
        self.assertEqual(catalogue_rows[8][2],100)
        response=self.client.get(reverse('executive_preview'))
        text=' '.join(p.extract_text() for p in PdfReader(BytesIO(b''.join(response.streaming_content))).pages)
        self.assertIn('Matriz institucional',text);self.assertIn('Taxa de emissão',text)
    def test_small_scope_keeps_four_redacted_cards_without_values(self):
        Employee.objects.filter(number='P0').update(direction='Private')
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'data-primary-kpi',count=4)
        self.assertContains(response,'grupo &lt;5',count=4)
        self.assertNotContains(response,'data-indicator=')
        self.assertNotContains(response,'Synthetic 0')
        self.assertNotContains(response,'H/D:')
    def test_unknown_and_zero_remain_distinct_and_mean_is_not_main_card(self):
        Training.objects.all().update(attendance=0)
        data,valid=context(SimpleNamespace(GET={}))
        cards=main_cards(data['stats'])
        self.assertEqual(cards[2]['value'],0)
        Training.objects.all().update(attendance_confirmed=False)
        data,valid=context(SimpleNamespace(GET={}))
        self.assertIsNone(main_cards(data['stats'])[2]['value'])
        self.assertNotIn('Média de desempenho',[c['label'] for c in cards])
    def test_manual_navigation_login_and_read_only(self):
        before=Training.objects.count()
        response=self.client.get(reverse('user_manual'))
        self.assertContains(response,'Os quatro cartões e os 12 indicadores')
        self.assertContains(self.client.get(reverse('assistant_admin')),'Manual de Utilização')
        self.assertEqual(self.client.post(reverse('user_manual')).status_code,405)
        self.assertEqual(Training.objects.count(),before)
        self.client.logout();self.assertEqual(self.client.get(reverse('user_manual')).status_code,302)

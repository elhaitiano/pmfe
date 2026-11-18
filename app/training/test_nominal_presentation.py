"""Presentation-only regressions with synthetic historical records."""
import re
from copy import copy
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import TestCase,override_settings
from django.urls import reverse
from . import test_historical as fixtures
from . import historical

@override_settings(PMF_HISTORICAL_ENABLED=True)
class NominalPresentationTests(TestCase):
    setUp=fixtures.HistoricalTests.setUp

    def numbered_rows(self,count):
        base=historical.records()[0];result=[]
        for i in range(count):
            row=base.copy();training=copy(base['training']);person=copy(self.employee)
            person.name=f'Synthetic order {count-i:02}';person.number=f'SYN-ORDER-{i:02}'
            training.employee=person;row['training']=training;result.append(row)
        return result

    def test_four_columns_preserve_input_order_and_all_details(self):
        rows=self.numbered_rows(3)
        html=render_to_string('planning/historical_nominal.html',{'historical_rows':rows})
        self.assertEqual(html.count('scope="col"'),4)
        self.assertEqual(re.findall(r'class="history-person-number">(\d+)',html),['1','2','3'])
        self.assertLess(html.index('Synthetic order 03'),html.index('Synthetic order 02'))
        for label in ['Período','Horas previstas','Nota / aproveitamento','Emissão do certificado','Entrega ao formando','Entrega aos RH','Verificação']:
            self.assertIn(label,html)
        self.assertNotIn('<details open',html)
        self.assertIn('scope="col">Assiduidade',html)
        self.assertIn('não representa classificação',html)

    def test_page_numbering_continues_without_reordering_or_new_pagination(self):
        rows=self.numbered_rows(23)
        page=Paginator(rows,20).get_page(2)
        html=render_to_string('planning/historical_nominal.html',{'historical_rows':page})
        self.assertEqual(re.findall(r'class="history-person-number">(\d+)',html),['21','22','23'])
        self.assertLess(html.index('SYN-ORDER-20'),html.index('SYN-ORDER-22'))

    def test_documented_attendance_and_unknown_keep_existing_semantics(self):
        row=historical.records()[0]
        html=render_to_string('planning/historical_nominal.html',{'historical_rows':[row]})
        self.assertIn('history-attendance unknown',html)
        row['training'].attendance_confirmed=True
        html=render_to_string('planning/historical_nominal.html',{'historical_rows':[row]})
        self.assertRegex(html,r'50(?:[,.]0)?%')
        row['training'].attendance=0
        html=render_to_string('planning/historical_nominal.html',{'historical_rows':[row]})
        self.assertRegex(html,r'>0(?:[,.]0)?%</span>')

    def test_detail_page_navigation_and_get_preserve_historical_records(self):
        from .models import Training
        self.client.force_login(self.admin)
        before=list(Training.objects.values())
        response=self.client.get(reverse('historical_detail',args=['SYN-E']))
        self.assertContains(response,'Lista Nominal')
        self.assertContains(response,reverse('employee_detail',args=[self.employee.pk]))
        self.assertNotContains(response,'<details open>')
        self.assertEqual(before,list(Training.objects.values()))

    def test_empty_state_has_four_column_span(self):
        html=render_to_string('planning/historical_nominal.html',{'historical_rows':[]})
        self.assertIn('colspan="4"',html)

    def test_pending_attendance_shortcut_opens_exact_record_without_writing(self):
        from .models import Training
        self.client.force_login(self.admin)
        before=list(Training.objects.values())
        response=self.client.get(reverse('historical_detail',args=['SYN-E']))
        target=reverse('training_edit',args=[self.training.pk])
        self.assertContains(response,target+'#id_attendance_confirmed')
        response=self.client.get(target)
        self.assertContains(response,'id="id_attendance_confirmed"')
        self.assertEqual(before,list(Training.objects.values()))

    def test_read_only_user_sees_pending_label_without_edit_shortcut(self):
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=['view_training','view_employee']))
        self.client.force_login(self.user)
        response=self.client.get(reverse('historical_detail',args=['SYN-E']))
        self.assertContains(response,'history-attendance unknown')
        target=reverse('training_edit',args=[self.training.pk])
        self.assertNotContains(response,target+'#id_attendance_confirmed')
        self.assertEqual(self.client.get(target).status_code,403)

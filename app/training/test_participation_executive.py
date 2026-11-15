from datetime import date
from io import BytesIO
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from .models import Employee, Training

class ParticipationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('report-reader')
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=['view_employee','view_training'],content_type__app_label='training'))
        self.client.force_login(self.user)
        self.url = reverse('participation_executive')
        self.a = Employee.objects.create(number='1',name='=Person A',direction='Operations')
        self.b = Employee.objects.create(number='2',name='Person B',direction='Operations')
        self.c = Employee.objects.create(number='3',name='Person C',direction='RH')
        common = dict(title='Course',start=date(2026,1,1),end=date(2026,2,10),days=2,hours_per_day=7,attendance=1,status='Concluído')
        Training.objects.create(employee=self.a,attendance_confirmed=True,**common)
        Training.objects.create(employee=self.a,attendance_confirmed=True,**common)
        Training.objects.create(employee=self.b,attendance_confirmed=False,**common)

    def test_distinct_people_and_verified_presence(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        report=response.context['report']
        self.assertEqual((report['universe'],report['participated'],report['completed']), (3,1,0))
        self.assertContains(response,'&lt;5')

    def test_search_pagination_do_not_change_denominator(self):
        response=self.client.get(self.url,{'direction_q':'RH','direction_page':'999','q':'Person A','detail_state':'participated'})
        self.assertEqual(response.context['report']['universe'],3)
        self.assertEqual(response.context['direction_page'].paginator.count,1)
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertTrue(response.context['open_nominal'])

    def test_start_date_and_invalid_filters(self):
        response=self.client.get(self.url,{'date_from':'2026-02-01','date_to':'2026-02-05'})
        self.assertEqual(response.context['report']['participated'],0)
        self.assertEqual(self.client.get(self.url,{'date_from':'2026-02-05','date_to':'2026-01-01'}).status_code,400)
        self.assertEqual(self.client.get(self.url,{'course':'999999'}).status_code,400)

    def test_nominal_direction_and_state(self):
        response=self.client.get(self.url,{'detail_direction':'Operations','detail_state':'absent'})
        self.assertEqual([r['employee'] for r in response.context['page']],[self.b])
        response=self.client.get(self.url,{'detail_state':'no_training'})
        self.assertEqual([r['employee'] for r in response.context['page']],[self.c])

    def test_export_full_table_and_safe_excel_text(self):
        response=self.client.get(self.url,{'export':'xlsx','direction_q':'RH','direction_page':'999'})
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(response.content))
        self.assertEqual(book.sheetnames,['Protecção'])
        self.assertNotIn('=Person A',book.active['A1'].value)
        response=self.client.get(self.url,{'export':'pdf'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_permissions_and_read_only(self):
        self.user.user_permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)

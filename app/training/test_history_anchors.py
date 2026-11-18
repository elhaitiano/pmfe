from datetime import date
import re
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Employee,Training

class HistoryAnchorTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='NAV-SYN',name='Formando sintético')
        self.reader=get_user_model().objects.create_user('navigation-reader',password='synthetic')
        self.admin=get_user_model().objects.create_superuser('navigation-admin',password='synthetic')
        self.rows=[Training.objects.create(employee=self.person,title='Formação semelhante',start=date(2026,1,1),end=date(2026,1,1),status='Por confirmar') for _ in range(2)]
    def login(self):self.client.force_login(self.reader)
    def test_distinct_ids_have_distinct_exact_targets(self):
        self.login();page=self.client.get(reverse('employee_data',args=[self.person.pk]));history=self.client.get(reverse('employee_detail',args=[self.person.pk]))
        for t in self.rows:
            self.assertContains(page,reverse('employee_detail',args=[self.person.pk])+'#participation-'+str(t.pk))
            self.assertContains(history,'id="participation-'+str(t.pk)+'"',count=1)
        self.assertEqual(len(re.findall(r'id="participation-\d+"',history.content.decode())),2)
    def test_eight_columns_and_no_style_structure_changes(self):
        self.login();response=self.client.get(reverse('employee_data',args=[self.person.pk]));text=response.content.decode()
        table=text.split('class="ficha-history-table"',1)[1].split('</table>',1)[0]
        self.assertEqual(len(re.findall(r'<th(?:\s|>)',table)),8)
    def test_anonymous_cannot_access_history_or_profile(self):
        for route in ['employee_data','employee_detail']:
            response=self.client.get(reverse(route,args=[self.person.pk]));self.assertEqual(response.status_code,302);self.assertIn('/entrar/',response.url)
    def test_reader_sees_history_but_not_editing_controls(self):
        self.login();response=self.client.get(reverse('employee_detail',args=[self.person.pk]));self.assertEqual(response.status_code,200)
        for t in self.rows:self.assertNotContains(response,reverse('training_delete',args=[t.pk]))
    def test_existing_admin_access_preserved(self):
        self.client.force_login(self.admin);response=self.client.get(reverse('employee_detail',args=[self.person.pk]));self.assertEqual(response.status_code,200)
        self.assertContains(response,reverse('training_edit',args=[self.rows[0].pk]))
    def test_unconfirmed_identity_policy_still_blocks_reader(self):
        self.login()
        with patch('training.data_validation.enabled',return_value=True),patch('training.data_validation.policy') as policy:
            policy.return_value.ok.return_value=False
            self.assertEqual(self.client.get(reverse('employee_detail',args=[self.person.pk])).status_code,403)
    def test_reading_changes_no_business_fields(self):
        self.login();before=list(Training.objects.order_by('pk').values())
        self.client.get(reverse('employee_data',args=[self.person.pk]));self.client.get(reverse('employee_detail',args=[self.person.pk]))
        self.assertEqual(before,list(Training.objects.order_by('pk').values()))
    def test_target_has_keyboard_and_fixed_header_support(self):
        self.login();response=self.client.get(reverse('employee_detail',args=[self.person.pk]))
        self.assertContains(response,'tabindex="-1" style="scroll-margin-top:7rem"',count=2)

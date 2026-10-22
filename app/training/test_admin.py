from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.contrib.staticfiles import finders
from django.test import TestCase, Client
from django.urls import reverse
from .admin import site
from .models import Employee, AuditEvent, CompetencyResponse, PrivateDocument, ExamAttempt


class AdministrationTests(TestCase):
    def setUp(self):
        self.root=get_user_model().objects.create_superuser('setup-admin',password='Test-only-Secret-8347!')
        self.manager=get_user_model().objects.create_user('setup-manager',is_staff=True)
        self.reader=get_user_model().objects.create_user('setup-reader')
        self.client.force_login(self.root)

    def test_bootstrap_index_and_lists(self):
        response=self.client.get(reverse('admin:index'))
        self.assertContains(response,'Primeiros registos')
        for model in ['employee','course','competency','roleprofile']:
            self.assertEqual(self.client.get(reverse(f'admin:training_{model}_changelist')).status_code,200)
        self.assertEqual(self.client.get(reverse('admin:auth_user_changelist')).status_code,200)
        self.assertContains(self.client.get(reverse('home')),'Administração')

    def test_admin_reserved_for_superuser(self):
        for user in [self.manager,self.reader]:
            self.client.force_login(user)
            response=self.client.get(reverse('admin:index'))
            self.assertEqual(response.status_code,302)
            self.assertNotContains(self.client.get(reverse('home')),'Administração')
            self.assertEqual(self.client.post(reverse('admin:auth_user_add'),{}).status_code,302)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('admin:index')).status_code,302)
        self.assertEqual(self.client.get(reverse('admin:login')).status_code,200)

    def test_business_writes_follow_existing_forms(self):
        mapping={'employee':'employee_create','course':'course_create',
                 'competency':'competency_create','roleprofile':'role_create'}
        for model,route in mapping.items():
            url=reverse(f'admin:training_{model}_add')
            self.assertRedirects(self.client.get(url),reverse(route))
            self.assertEqual(self.client.post(url,{}).status_code,405)
        person=Employee.objects.create(number='ADMIN',name='Teste')
        change=reverse('admin:training_employee_change',args=[person.pk])
        self.assertRedirects(self.client.get(change),reverse('employee_edit',args=[person.pk]))
        self.assertEqual(self.client.post(change,{'name':'Bypass'}).status_code,405)
        person.refresh_from_db();self.assertEqual(person.name,'Teste')
        self.assertEqual(self.client.get(reverse('admin:training_employee_delete',args=[person.pk])).status_code,403)

    def test_create_account_hashes_password_and_audits_without_secret(self):
        secret='Test-only-New-Account-4782!'
        response=self.client.post(reverse('admin:auth_user_add'),{
            'username':'new-evaluator','usable_password':'true','password1':secret,'password2':secret,'_save':'Guardar'})
        self.assertEqual(response.status_code,302)
        user=get_user_model().objects.get(username='new-evaluator')
        self.assertTrue(user.check_password(secret))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(AuditEvent.objects.filter(action='ADMIN_ACCOUNT').exists())
        self.assertFalse(AuditEvent.objects.filter(detail__contains=secret).exists())
        self.assertFalse(LogEntry.objects.filter(change_message__contains=secret).exists())

    def test_update_roles_deactivate_and_preserve_own_access(self):
        url=reverse('admin:auth_user_change',args=[self.reader.pk])
        data={'username':self.reader.username,'is_active':'on','is_staff':'on','_save':'Guardar'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.reader.refresh_from_db();self.assertTrue(self.reader.is_staff)
        data.pop('is_active')
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.reader.refresh_from_db();self.assertFalse(self.reader.is_active)
        own=reverse('admin:auth_user_change',args=[self.root.pk])
        self.assertEqual(self.client.post(own,{'username':self.root.username,'_save':'Guardar'}).status_code,302)
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_active and self.root.is_staff and self.root.is_superuser)
        self.assertEqual(self.client.post(reverse('admin:auth_user_delete',args=[self.reader.pk]),{'post':'yes'}).status_code,403)

    def test_private_records_not_registered_and_csrf_enforced(self):
        for model in [CompetencyResponse,PrivateDocument,ExamAttempt]:
            self.assertFalse(site.is_registered(model))
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.root)
        self.assertEqual(strict.post(reverse('admin:auth_user_add'),{}).status_code,403)
        self.assertIsNotNone(finders.find('admin/css/base.css'))

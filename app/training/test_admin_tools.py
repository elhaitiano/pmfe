from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

URL = '/administracao/ferramentas/'


class AdministrationToolsTests(TestCase):
    def user(self, name, **flags):
        return get_user_model().objects.create_user(name, password='x-Synthetic-2026', **flags)

    def test_route_name(self):
        self.assertEqual(reverse('ux_administration'), URL)

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(URL, response['Location'].split('?')[0])

    def test_regular_user_has_no_access_and_no_menu_entry(self):
        self.client.force_login(self.user('regular'))
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertNotContains(self.client.get(reverse('home')), 'id="navAdministracao"')

    def test_staff_sees_activity_log_only(self):
        self.client.force_login(self.user('staff', is_staff=True))
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ferramentas administrativas')
        self.assertContains(response, 'Registo de actividade')
        self.assertNotContains(response, 'Contas e manutenção')

    def test_staff_with_permissions_sees_audit_and_documents(self):
        staff = self.user('auditor', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=['view_auditevent', 'add_privatedocument']))
        self.client.force_login(staff)
        response = self.client.get(URL)
        self.assertContains(response, '>Auditoria<')
        self.assertNotContains(response, 'Registo de actividade')
        self.assertContains(response, 'Documentação dos formandos')
        self.assertContains(response, f'href="{reverse("employee_data_select")}"')

    def test_superuser_sees_every_tool_whose_destination_exists(self):
        self.client.force_login(self.user('root', is_staff=True, is_superuser=True))
        response = self.client.get(URL)
        for label in ['Auditoria', 'Documentação dos formandos', 'Contas e manutenção']:
            self.assertContains(response, label)
        self.assertContains(response, f'href="{reverse("admin:index")}"')
        # Destinations that do not exist in this installation are not shown as empty links.
        for label in ['Validação de grupos', 'Pendências e divergências', 'Regularização documental']:
            self.assertNotContains(response, label)

    def test_every_button_opens(self):
        self.client.force_login(self.user('root2', is_staff=True, is_superuser=True))
        response = self.client.get(URL)
        for tool in response.context['tools']:
            with self.subTest(tool=tool['route']):
                self.assertEqual(self.client.get(tool['url']).status_code, 200)

    def test_menu_points_to_the_tools_page_and_breadcrumb(self):
        self.client.force_login(self.user('root3', is_staff=True, is_superuser=True))
        response = self.client.get(URL)
        self.assertContains(response, f'href="{URL}" id="navAdministracao"')
        self.assertContains(response, '<li aria-current="page">Administração e Definições</li>', html=False)

    def test_post_is_not_allowed(self):
        self.client.force_login(self.user('root4', is_staff=True, is_superuser=True))
        self.assertEqual(self.client.post(URL).status_code, 405)

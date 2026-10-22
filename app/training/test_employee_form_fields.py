from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .forms import EmployeeForm
from .models import Employee


class EmployeeFormFieldsTests(TestCase):
    def test_edit_roundtrip_and_pages_use_columns(self):
        self.client.force_login(get_user_model().objects.create_user('editor', is_staff=True))
        employee = Employee.objects.create(number='E123', name='Teste',
            source_data={'Sexo': 'Antigo', 'Nº do BI': 'Antigo', 'Validação Cadastro EPAL': 'Antigo', 'extra': 'keep'})
        data = dict(number='E123', name='Teste', gender='F', identity_number='000123LA045',
            social_security_number='0012345', marital_status='Casado(A)', company='EPAL',
            direction='Direcção técnica', function='Operadora', category='Técnica',
            area='Zona Norte', cost_center='CC-002', registration_origin='Mapa EPAL',
            review_status='Apto', registration_validation='Confirmado', source_row=20,
            phone_secondary='923456789')
        response = self.client.post(reverse('employee_edit', args=[employee.pk]), data)
        self.assertEqual(response.status_code, 302)
        employee.refresh_from_db()
        for name, value in data.items():
            if name != 'phone_secondary':
                self.assertEqual(getattr(employee, name), value)
        self.assertEqual(employee.source_data['extra'], 'keep')
        form = EmployeeForm(instance=employee)
        rendered_fields = [field.name for _, fields in form.design_groups for field in fields]
        self.assertCountEqual(rendered_fields, form.fields)
        for field in form.fields.values():
            self.assertEqual(field.widget.attrs['class'], 'gf-field')
        for route in ['employee_data', 'employee_print']:
            response = self.client.get(reverse(route, args=[employee.pk]))
            for value in ['000123LA045', '0012345', 'CC-002', 'Zona Norte', 'Mapa EPAL', 'Confirmado']:
                self.assertContains(response, value)
        response = self.client.get(reverse('employee_edit', args=[employee.pk]))
        for name in form.fields:
            self.assertContains(response, f'name="{name}"')
        data['registration_validation'] = ''
        self.client.post(reverse('employee_edit', args=[employee.pk]), data)
        employee.refresh_from_db()
        self.assertNotEqual(employee.validation_status, 'Antigo')

    def test_invalid_source_row_is_rejected_and_minimal_creation_allowed(self):
        form = EmployeeForm(data={'number': 'E2', 'name': 'Teste', 'source_row': 0})
        self.assertFalse(form.is_valid())
        self.assertIn('source_row', form.errors)
        form = EmployeeForm(data={'number': 'E2', 'name': 'Teste'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().source_row)

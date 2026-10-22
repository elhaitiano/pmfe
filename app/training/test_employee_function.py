from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase
from .analytics import profile_fields
from .forms import EmployeeForm
from .models import Employee


class EmployeeFunctionTests(TestCase):
    def test_form_uses_column_in_requested_order_and_allows_clearing(self):
        employee = Employee.objects.create(number='F001', name='Técnica',
            function='Operadora', source_data={'Função': 'Valor antigo', 'Sexo': 'F'})
        form = EmployeeForm(instance=employee)
        fields = list(form.fields)
        self.assertEqual(fields[fields.index('direction'):fields.index('category') + 1],
                         ['direction', 'function', 'category'])
        self.assertEqual(form['function'].value(), 'Operadora')
        for value in ['Chefe de equipa', '']:
            form = EmployeeForm(instance=employee, data={
                'number': employee.number, 'name': employee.name, 'function': value,
            })
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            employee.refresh_from_db()
            self.assertEqual(employee.function, value)
            self.assertEqual(employee.source_data['Sexo'], 'F')
            self.assertEqual(dict(profile_fields(employee))['Função'], value)

    def test_backfill_preserves_legacy_values_and_reverse_preserves_edits(self):
        existing = Employee.objects.create(number='F002', name='Técnico',
            source_data={'Função': 'Operador', 'Outra coluna': 'original'})
        empty = Employee.objects.create(number='F003', name='Sem função')
        migration = import_module('training.migrations.0012_employee_function')
        editor = SimpleNamespace(connection=connection)
        migration.copy_function(apps, editor)
        existing.refresh_from_db()
        empty.refresh_from_db()
        self.assertEqual(existing.function, 'Operador')
        self.assertEqual(empty.function, '')
        existing.function = 'Chefe'
        existing.save(update_fields=['function'])
        migration.preserve_function_in_source(apps, editor)
        existing.refresh_from_db()
        self.assertEqual(existing.source_data['Função'], 'Chefe')
        self.assertEqual(existing.source_data['Outra coluna'], 'original')

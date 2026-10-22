from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class EmployeeExcelMigrationTests(TransactionTestCase):
    def test_existing_profiles_survive_forward_and_reverse_migration(self):
        before = [('training', '0012_employee_function')]
        after = [('training', '0013_employee_excel_fields')]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            Employee = executor.loader.project_state(before).apps.get_model('training', 'Employee')
            original = {'Sexo': 'F', 'Nº do BI': '000123LA045', 'N.º INSS': '001234',
                        'Estado Civil': 'Casado(A)', 'Empresa': 'EPAL',
                        'Centro de Custo': 'CC01', 'Origem do cadastro': 'Mapa EPAL',
                        'Estado de revisão': 'Apto', 'Validação Cadastro EPAL': 'Definitivo',
                        'Linha fonte EPAL': 20.0, 'Função exercida': 'Operadora',
                        'Outra coluna': 'preservar'}
            employee = Employee.objects.create(number='EX01', name='Teste', area='Área distinta', source_data=original)
            Employee.objects.create(number='EX02', name='Outro', function='Função editada',
                                    source_data={'Função exercida': 'Antiga', 'Linha fonte EPAL': 'inválida'})
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            Employee = executor.loader.project_state(after).apps.get_model('training', 'Employee')
            employee = Employee.objects.get(pk=employee.pk)
            expected = dict(gender='F', identity_number='000123LA045', social_security_number='001234',
                            marital_status='Casado(A)', company='EPAL', cost_center='CC01',
                            registration_origin='Mapa EPAL', review_status='Apto',
                            registration_validation='Definitivo', source_row=20, function='Operadora')
            for name, value in expected.items():
                self.assertEqual(getattr(employee, name), value)
            self.assertEqual(employee.area, 'Área distinta')
            self.assertEqual(employee.source_data, original)
            other = Employee.objects.get(number='EX02')
            self.assertIsNone(other.source_row)
            self.assertEqual(other.function, 'Função editada')
            employee.identity_number = '000456LA045'
            employee.save(update_fields=['identity_number'])
            executor = MigrationExecutor(connection)
            executor.migrate(before)
            Employee = executor.loader.project_state(before).apps.get_model('training', 'Employee')
            restored = Employee.objects.get(number='EX01')
            self.assertEqual(restored.source_data['Nº BI'], '000456LA045')
            self.assertEqual(restored.source_data['Outra coluna'], 'preservar')
        finally:
            MigrationExecutor(connection).migrate(after)

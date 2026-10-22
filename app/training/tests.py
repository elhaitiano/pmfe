import json
import sqlite3
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from .models import AuditEvent, Employee, Training


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user('manager', password='A-test-only-password-734', is_staff=True)
        cls.reader = get_user_model().objects.create_user('reader', password='A-test-only-password-734')
        cls.employee = Employee.objects.create(number='0012', name='Ana Exemplo')
        cls.other = Employee.objects.create(number='0013', name='Ana Exemplo')

    def test_data_menu_opens_profile_before_printing(self):
        self.client.force_login(self.reader)
        self.create_training()
        response = self.client.get(reverse('employee_data', args=[self.employee.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FICHA INDIVIDUAL DO FORMANDO')
        self.assertContains(response, 'Dados do Formando')
        self.assertContains(response, 'person</span>Ficha Individual')
        self.assertContains(response, '1 entrega por confirmar')
        self.assertContains(response, reverse('employee_print', args=[self.employee.pk]))
        self.assertContains(response, 'Segurança')
        printed = self.client.get(reverse('employee_print', args=[self.employee.pk]))
        self.assertContains(printed, reverse('employee_data', args=[self.employee.pk]))
        self.assertContains(printed, 'epal-oficial.png')

    def test_print_layout_preserves_platform_details(self):
        self.client.force_login(self.admin)
        employee = self.employee
        employee.name = 'Ana Maria de Exemplo'
        employee.area = 'CENTRO TESTE'
        employee.phone = '923255256'
        employee.function = 'Operadora'
        employee.source_data = {'Função': 'Operadora', 'Telefone 2': '924123456'}
        employee.save()
        self.create_training()
        printed = self.client.get(reverse('employee_print', args=[employee.pk]))
        self.assertEqual(printed.context['print_name'], 'Ana Exemplo')
        fields = dict(printed.context['print_fields'])
        self.assertEqual(fields['Função'], 'Operadora')
        self.assertEqual(fields['Área Alocada'], 'CENTRO TESTE')
        self.assertEqual(fields['Centro de Custo'], '')
        for label in ['N.º BI', 'N.º INSS', 'Género', 'Estado civil', 'Empresa',
                      'Origem do cadastro', 'Estado de revisão', 'Validação Cadastro EPAL']:
            self.assertIn(label, fields)
        self.assertContains(printed, '924 123 456')
        self.assertContains(printed, 'CENTRO TESTE')
        self.assertContains(printed, '<th>CATEGORIA</th>', html=True)
        self.assertNotContains(printed, '<th>RESULTADO</th>')
        self.assertNotContains(printed, 'CERTIFICADO ENTREGUE')
        self.assertNotContains(printed, 'N.º 0012<br>')
        profile = self.client.get(reverse('employee_data', args=[employee.pk]))
        self.assertContains(profile, 'Ana Maria de Exemplo')
        self.assertContains(profile, 'CENTRO TESTE')
        from .forms import EmployeeForm
        form = EmployeeForm(instance=employee, data={'number':employee.number,
            'name':employee.name, 'phone_secondary':'925000111', 'function':'Técnica'})
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.source_data['Telefone 2'], '925000111')
        self.assertEqual(updated.source_data['Função'], 'Técnica')

    def test_employee_edit_preserves_source_and_requires_staff(self):
        self.client.force_login(self.reader)
        url = reverse('employee_edit', args=[self.employee.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        self.employee.source_data = {'Sexo':'F', 'Nº do BI':'BI-TESTE'}
        self.employee.save()
        response = self.client.post(url, {'number':'0012', 'name':'Ana Maria de Exemplo',
            'function':'Operadora', 'phone_secondary':'924123456'})
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.source_data['Sexo'], 'F')
        self.assertEqual(self.employee.source_data['Telefone 2'], '924123456')
        self.assertTrue(AuditEvent.objects.filter(action='EDIT_EMPLOYEE').exists())

    def test_data_selection_uses_employee_id_and_requires_login(self):
        self.assertEqual(self.client.get(reverse('employee_data_select')).status_code, 302)
        self.client.force_login(self.reader)
        response = self.client.get(reverse('employee_data_select'), {'q': '0013'})
        self.assertContains(response, reverse('employee_data', args=[self.other.pk]))
        self.assertNotContains(response, reverse('employee_data', args=[self.employee.pk]))
        self.assertEqual(self.client.get(reverse('employee_data', args=[999999])).status_code, 404)

    def test_reference_charts_keep_unknown_grade_separate_from_zero(self):
        self.client.force_login(self.reader)
        training = self.create_training()
        response = self.client.get(reverse('employee_data', args=[self.employee.pk]))
        self.assertContains(response, 'MAPA DE COMPETÊNCIAS TÉCNICAS')
        self.assertContains(response, 'HISTÓRICO RECENTE DE FORMAÇÃO')
        self.assertContains(response, 'pmf-donut-segment')
        self.assertContains(response, 'Sem nota')
        self.assertIsNone(response.context['competencies'][0]['percent'])
        self.assertEqual(response.context['distribution'][0]['percent'], 100)
        training.grade = Decimal('0')
        training.save()
        response = self.client.get(reverse('employee_data', args=[self.employee.pk]))
        self.assertEqual(response.context['competencies'][0]['percent'], 0)
        self.assertContains(response, '0,0/20')
        training.grade = Decimal('16')
        training.save()
        response = self.client.get(reverse('employee_data', args=[self.employee.pk]))
        self.assertEqual(response.context['competencies'][0]['percent'], 80)
        self.assertContains(response, '16,0/20')

    def test_weighted_attendance_planned_exclusion_and_independent_grades(self):
        from .analytics import statistics
        first = self.create_training()
        first.grade = Decimal('0'); first.certified = True
        first.certificate_delivery_date = date(2025, 1, 4); first.save()
        Training.objects.create(employee=self.employee, title='Outra', area='Gestão',
            start=date(2025,2,1),end=date(2025,2,9),days=9,hours_per_day=2,attendance=1,grade=16,certified=False)
        planned = Training.objects.create(employee=self.employee,title='Planeada',start=date(2027,1,1),
            end=date(2027,1,4),days=4,hours_per_day=6,attendance=0,status='Planeado')
        result = statistics(self.employee.trainings.all())
        self.assertEqual(result['attendance'], Decimal('25.0'))
        self.assertEqual(result['hours'], Decimal('17'))
        self.assertEqual(result['planned_hours'], Decimal('64.5'))
        self.assertEqual(result['average'], Decimal('8'))
        self.assertEqual(result['certificates'], 1)
        self.assertEqual(result['certificates_pending'], 1)
        self.assertEqual(result['certificates_not_delivered'], 1)
        self.assertIsNone(planned.attendance_percent)
        self.assertIsNone(planned.absences)
        self.assertEqual(first.absences, 1)

    def test_empty_metrics_and_rounded_distribution(self):
        from .analytics import statistics, distribution
        self.assertIsNone(statistics([])['attendance'])
        self.assertIsNone(statistics([])['average'])
        self.assertEqual(distribution([]), [])
        rows = [Training(employee=self.employee,title=area,area=area,start=date(2025,1,1),
            end=date(2025,1,1),days=1,hours_per_day=Decimal('7'),attendance=1) for area in ['A','B','C']]
        result = distribution(rows)
        self.assertEqual(sum(x['display_percent'] for x in result), 100)
        self.assertEqual([x['display_percent'] for x in result], [34,33,33])
        self.assertEqual(sum(x['hours'] for x in result), 21)

    def test_certificate_states_and_delivery_chronology(self):
        self.client.force_login(self.admin)
        url = reverse('training_create', args=[self.employee.pk])
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        for changes in [{'certificate_delivery_date':''}, {'certificate_delivery_date':'2024-12-31'},
                        {'certificate_delivery_date':future}, {'certified':'false'},
                        {'status':'Em Curso'}, {'status':'Planeado', 'attendance':0}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(url,self.payload(**changes)).status_code,200)
                self.assertFalse(Training.objects.exists())
        response = self.client.post(url,self.payload(certified='unknown',certificate_delivery_date='',grade=''))
        self.assertEqual(response.status_code,302)
        training = Training.objects.get()
        self.assertIsNone(training.certified)
        self.assertEqual(training.certificate_label, 'Por confirmar')
        history = self.client.get(reverse('employee_detail',args=[self.employee.pk]))
        self.assertContains(history, 'Certificado entregue: Por confirmar')

    def test_new_form_is_planned_and_counters_are_shared_with_print(self):
        self.client.force_login(self.admin)
        form = self.client.get(reverse('training_create',args=[self.employee.pk])).context['form']
        self.assertEqual(form.initial['status'], 'Planeado')
        self.assertEqual(form.initial['certified'], 'unknown')
        self.create_training()
        profile = self.client.get(reverse('employee_data',args=[self.employee.pk]))
        printed = self.client.get(reverse('employee_print',args=[self.employee.pk]))
        self.assertEqual(profile.context['stats'], printed.context['stats'])
        self.assertEqual(profile.context['distribution'], printed.context['distribution'])
        self.assertContains(printed, 'Função:')
        self.assertContains(printed, 'Centro de Custo')
        self.assertContains(printed, 'por confirmar')

    def test_profile_has_recent_five_and_history_keeps_all(self):
        self.client.force_login(self.reader)
        for i in range(8):
            Training.objects.create(employee=self.employee,title=f'Formação {i}',area='Técnica',
                start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1)
        profile = self.client.get(reverse('employee_data',args=[self.employee.pk]))
        self.assertEqual(len(profile.context['trainings']),5)
        self.assertEqual(profile.context['stats']['total'],8)
        history = self.client.get(reverse('employee_detail',args=[self.employee.pk]))
        self.assertEqual(len(history.context['trainings']),8)
        self.assertNotContains(history, 'MAPA ILUSTRATIVO')
        self.assertNotContains(history, 'Conquistas')

    def payload(self, **changes):
        values = {'title': 'Segurança', 'area': 'Operações', 'start': '2025-01-01',
                  'end': '2025-01-03', 'days': 3, 'hours_per_day': '7.5',
                  'attendance': 2, 'grade': '0', 'status': 'Concluído', 'certified': 'true', 'certificate_delivery_date':'2025-01-05', 'result':'Pendente'}
        return values | changes

    def create_training(self):
        return Training.objects.create(employee=self.employee, title='Segurança', area='Operações',
            start=date(2025, 1, 1), end=date(2025, 1, 3), days=3, hours_per_day=Decimal('7.5'), attendance=2)

    def test_authentication_and_logout(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertTrue(self.client.login(username='manager', password='A-test-only-password-734'))
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/sair/').status_code, 405)
        self.client.post('/sair/')
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_original_login_form_uses_real_authentication_and_session_choice(self):
        response = self.client.post('/entrar/', {'username': 'manager', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        credentials = {'username': 'manager', 'password': 'A-test-only-password-734'}
        response = self.client.post('/entrar/', credentials)
        self.assertRedirects(response, '/')
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.client.post('/sair/')
        self.client.post('/entrar/', credentials | {'remember': 'on'})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_history_filters_keep_employee_identity_and_recalculate_indicators(self):
        self.create_training()
        Training.objects.create(employee=self.employee, title='Outra área', area='Gestão',
            start=date(2024, 1, 1), end=date(2024, 1, 1), days=1, hours_per_day=8, attendance=1)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('employee_detail', args=[self.employee.pk]), {'year': '2025', 'area': 'Operações'})
        self.assertEqual(response.context['stats']['total'], 1)
        self.assertEqual(response.context['stats']['hours'], Decimal('15'))
        self.assertEqual(response.context['employee'].number, '0012')
        response = self.client.get('/', {'employee': self.other.pk})
        self.assertEqual(response.context['selected_stats']['total'], 0)

    def test_local_design_assets_are_served_without_exposing_database(self):
        for path, mime in [('tailwind.css', 'text/css'), ('fonts.css', 'text/css'),
                           ('fonts/font-0-0.ttf', 'font/ttf'), ('epal-logo.png', 'image/png')]:
            with self.subTest(path=path):
                response = self.client.get('/static/pmf/' + path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response['Content-Type'].startswith(mime))
                response.close()
        self.assertEqual(self.client.get('/static/data/pmf.sqlite3').status_code, 404)

    def test_reader_cannot_mutate_or_export_database(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.post(reverse('training_create', args=[self.employee.pk]), self.payload()).status_code, 403)
        self.assertFalse(Training.objects.exists())
        self.assertEqual(self.client.post('/backup/').status_code, 403)

    def test_csrf_required_for_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(reverse('training_create', args=[self.employee.pk]), self.payload()).status_code, 403)

    def test_create_calculations_identity_and_audit(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('training_create', args=[self.employee.pk]), self.payload())
        self.assertEqual(response.status_code, 302)
        training = Training.objects.get()
        self.assertEqual(training.completed_hours, Decimal('15'))
        self.assertEqual(training.attendance_percent, Decimal('66.7'))
        self.assertEqual(training.grade, 0)
        self.assertFalse(self.other.trainings.exists())
        self.assertTrue(AuditEvent.objects.filter(action='ADD_TRAINING').exists())
        self.assertContains(self.client.get(reverse('employee_detail', args=[self.employee.pk])), 'Segurança')

    def test_invalid_values_are_rejected(self):
        self.client.force_login(self.admin)
        future = (timezone.localdate() + timedelta(days=2)).isoformat()
        for changes in ({'attendance': 4}, {'attendance': -1}, {'days': 0}, {'grade': 21},
                        {'hours_per_day': 25}, {'end': '2024-12-31'}, {'end': future}):
            with self.subTest(changes=changes):
                response = self.client.post(reverse('training_create', args=[self.employee.pk]), self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Training.objects.exists())

    def test_blank_grade_is_not_zero(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('training_create', args=[self.employee.pk]), self.payload(grade=''))
        self.assertIsNone(Training.objects.get().grade)

    def test_edit_and_stale_edit_protection(self):
        training = self.create_training()
        self.client.force_login(self.admin)
        url = reverse('training_edit', args=[training.pk])
        self.assertEqual(self.client.post(url, self.payload(title='Actualizada', revision=1)).status_code, 302)
        response = self.client.post(url, self.payload(title='Antiga', revision=1))
        self.assertContains(response, 'noutra janela')
        training.refresh_from_db()
        self.assertEqual(training.title, 'Actualizada')
        self.assertEqual(training.version, 2)

    def test_delete_confirmation_preserves_employee(self):
        training = self.create_training()
        self.client.force_login(self.admin)
        url = reverse('training_delete', args=[training.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(Training.objects.exists())
        self.client.post(url)
        self.assertFalse(Training.objects.exists())
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action='DELETE_TRAINING').exists())

    def test_search_print_and_offline_interface(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/?q=0012'), '0012')
        self.assertNotContains(self.client.get('/?q=0012'), '0013')
        response = self.client.get(reverse('employee_print', args=[self.employee.pk]))
        self.assertContains(response, 'Ficha individual')
        self.assertNotContains(response, 'https://')
        self.assertEqual(self.client.get('/auditoria/').status_code, 200)

    def test_backup_is_a_restorable_sqlite_snapshot(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/backup/').status_code, 405)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.sqlite3'
            restored = Path(folder) / 'restored.sqlite3'
            with sqlite3.connect(source) as connection:
                connection.execute('CREATE TABLE example (value TEXT)')
                connection.execute('INSERT INTO example VALUES (?)', ('snapshot',))
            config = SimpleNamespace(DATABASES={'default': {'NAME': source}})
            with patch('training.views.settings', config):
                response = self.client.post('/backup/')
                self.assertEqual(response.status_code, 200)
                restored.write_bytes(b''.join(response.streaming_content))
                response.close()
            with sqlite3.connect(restored) as connection:
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(connection.execute('SELECT value FROM example').fetchone()[0], 'snapshot')


class ImportTests(TestCase):
    def workbook(self):
        # A read-only workbook stub; no source spreadsheet is authored by this test.
        employee_headers = ['N.º', 'N.º Mecan', 'N.º INSS', 'Nome', 'Nascimento', 'Sexo', 'BI',
                            'Estado', 'Telefone', 'Admissão', 'Categoria', 'Email', 'Direcção', 'Área']
        training_headers = ['N.º Mecanográfico', 'Nome Formação', 'Data de Início', 'Data de Fim',
                            'Duração (dias)', 'Nº Presenças', 'Nº Hora por dia', 'Área/Categoria', 'Status-Curso']

        class Sheet:
            def __init__(self, title, headers, rows):
                self.title, self.headers, self.rows = title, headers, rows

            def iter_rows(self, min_row, max_row=None, values_only=True):
                return iter([self.headers] if max_row else self.rows)

        class Workbook(dict):
            @property
            def sheetnames(self):
                return list(self)

            def close(self):
                pass

        return Workbook({
            'FUNCIONÁRIOS_ EPAL': Sheet('FUNCIONÁRIOS_ EPAL', employee_headers,
                [[1, '001', None, 'Teste', '01-01-1990', None, None, None, '', '01-01-2010', '', '', '', '']]),
            'Formandos': Sheet('Formandos', training_headers, [
                ['001', 'Teste formação', '2025-01-01', '2025-01-02', 2, 1, 7, 'Área', 'Concluído'],
                ['999', 'Sem identidade', '2025-01-01', '2025-01-02', 2, 1, 7, 'Área', 'Concluído']])})

    @patch('training.management.commands.import_excel.load_workbook')
    def test_dry_run_apply_idempotence_and_unmatched(self, load):
        load.side_effect = lambda *args, **kwargs: self.workbook()
        with tempfile.TemporaryDirectory() as folder:
            source, report = Path(folder) / 'source.xlsm', Path(folder) / 'report.json'
            source.write_bytes(b'test-source-never-edited')
            call_command('import_excel', source, report=report, stdout=StringIO())
            self.assertFalse(Employee.objects.exists())
            result = json.loads(report.read_text())
            self.assertEqual(result['employees_created'], 1)
            self.assertEqual(len(result['issues']), 1)
            call_command('import_excel', source, report=report, apply=True, stdout=StringIO())
            call_command('import_excel', source, report=report, apply=True, stdout=StringIO())
            self.assertEqual(Employee.objects.count(), 1)
            self.assertEqual(Training.objects.count(), 1)
            self.assertEqual(source.read_bytes(), b'test-source-never-edited')

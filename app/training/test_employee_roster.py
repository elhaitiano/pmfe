"""Synthetic roster matching the supplied layout/volume; no real identities."""
from datetime import date
from io import BytesIO
from decimal import Decimal
from django.test import TestCase
from openpyxl import Workbook
from .excel_sources import load_source, parse_date
from .integrated_import import build_plan, apply_reviewed
from .models import Employee, Training, AuditEvent
from .analytics import statistics

COUNT = 1510
HEADERS = ['N.º','N.º Mecan','N.º INSS','Sexo','Nome','Data de Nascimeto',
           'Nº do BI','Estado Civil','N.º de Telefoe','Data de Admissão',
           'Categoria','Função','Direcção e Gabinete','Área Alocada']

def roster(count=COUNT):
    book=Workbook();sheet=book.active;sheet.title='FUNCIONÁRIOS_ EPAL'
    for _ in range(10):sheet.append([None])
    sheet.append(HEADERS)
    for i in range(1,count+1):
        sheet.append([i,f'TEST-{i:04}',f'TEST-INSS-{i:04}','F' if i<=367 else 'M',
            f'Pessoa sintética {i:04}',None if i==1 else '24.08.1983' if i==2 else date(1980,1,1),
            f'TEST-BI-{i:04}',None if i==1 else 'SOLTEIRO(A)',
            None if i<=985 else '000000000',date(2010,1,1),
            f'Categoria {(i-1)%173}',f'Função {(i-1)%20}',f'Direcção {(i-1)%33}','Área sintética'])
    book.create_sheet('Folha1')
    output=BytesIO();book.save(output)
    return load_source(BytesIO(output.getvalue()))

class EmployeeRosterTests(TestCase):
    def test_dotted_date_supported_but_invalid_calendar_date_rejected(self):
        self.assertEqual(parse_date('24.08.1983'),date(1983,8,24))
        with self.assertRaises(ValueError):parse_date('31.02.1983')

    def test_large_sparse_roster_preserves_existing_identity_history_and_is_idempotent(self):
        for i in range(1,101):
            Employee.objects.create(number=f'TEST-{i:04}',name=f'Pessoa sintética {i:04}')
        first=Employee.objects.get(number='TEST-0001')
        training=Training.objects.create(employee=first,title='Synthetic training',start=date(2025,1,1),
            end=date(2025,1,1),days=1,hours_per_day=6,attendance=1,attendance_confirmed=True)
        book=roster();self.addCleanup(book.close)
        plan=build_plan(book,'synthetic-roster')
        self.assertTrue(plan.report['can_apply'],plan.report['issues'][:3])
        self.assertEqual(plan.report['employees_created'],1410)
        self.assertEqual(plan.report['employees_updated'],100)
        self.assertEqual(Employee.objects.count(),100)  # Preview is read-only.
        apply_reviewed(book,'synthetic-roster',plan.report['review_token'])
        self.assertEqual(Employee.objects.count(),COUNT)
        self.assertEqual(Training.objects.count(),1)
        training.refresh_from_db()
        self.assertEqual(training.employee_id,first.pk)
        self.assertEqual(Employee.objects.filter(phone='').count(),985)
        self.assertEqual(Employee.objects.filter(birth_date=None).count(),1)
        self.assertEqual(Employee.objects.filter(marital_status='').count(),1)
        self.assertEqual(Employee.objects.exclude(function='').count(),COUNT)
        self.assertEqual(Employee.objects.exclude(email='').count(),0)
        first.refresh_from_db()
        self.assertEqual(first.source_data['_import']['sources'][0]['row'],12)
        summary=statistics(Training.objects.all(),Employee.objects.all())['kpi_summary']
        self.assertEqual(summary.metrics['coverage'].denominator,COUNT)
        self.assertEqual(summary.metrics['coverage'].value,Decimal('0.07'))
        self.assertEqual(summary.metrics['hours'].value,6)
        repeated=build_plan(book,'same-content-renamed')
        self.assertEqual(repeated.report['employees_created'],0)
        self.assertEqual(repeated.report['employees_updated'],0)
        apply_reviewed(book,'same-content-renamed',repeated.report['review_token'])
        self.assertEqual(Employee.objects.count(),COUNT)
        self.assertEqual(Training.objects.get().employee_id,first.pk)

    def test_format_variants_preserve_values_but_real_changes_remain_blocked(self):
        employee=Employee.objects.create(number='TEST-0002',name='Pessoa sintética 0002',
            marital_status='Solteiro(A)',direction='Direccao 1',category='Categoria 1')
        book=roster(2);self.addCleanup(book.close)
        plan=build_plan(book,'format')
        self.assertTrue(plan.report['can_apply'],plan.report['issues'])
        self.assertEqual(sum(i['reason']=='Grafia equivalente; valor existente conservado.' for i in plan.report['issues']),2)
        obj=next(obj for obj,kind in plan.employees if obj.number==employee.number)
        self.assertEqual(obj.direction,'Direccao 1')
        employee.category='Categoria diferente';employee.save()
        plan=build_plan(book,'real-change')
        self.assertFalse(plan.report['can_apply'])
        self.assertTrue(any(i.get('field')=='category' and i['severity']=='error' for i in plan.report['issues']))
        self.assertFalse(AuditEvent.objects.exists())

    def test_replacements_require_explicit_fields_current_token_and_keep_audit(self):
        employee=Employee.objects.create(number='TEST-0002',name='Pessoa sintética 0002',category='Anterior',
                                        source_data={'original':'Preserve evidence'})
        book=roster(2);self.addCleanup(book.close)
        strict=build_plan(book,'replacement')
        self.assertFalse(strict.report['can_apply'])
        reviewed=build_plan(book,'replacement',{'category'})
        self.assertTrue(reviewed.report['can_apply'])
        self.assertNotEqual(reviewed.report['review_token'],strict.report['review_token'])
        with self.assertRaises(ValueError):
            apply_reviewed(book,'replacement',strict.report['review_token'],replace_employee_fields={'category'})
        employee.refresh_from_db();self.assertEqual(employee.category,'Anterior')
        apply_reviewed(book,'replacement',reviewed.report['review_token'],replace_employee_fields={'category'})
        employee.refresh_from_db();self.assertEqual(employee.category,'Categoria 1')
        import json
        audit=json.loads(AuditEvent.objects.get(action='IMPORT_EMPLOYEE_UPDATE').detail)
        self.assertEqual(audit['fields']['category'],{'before':'Anterior','after':'Categoria 1'})
        self.assertEqual(audit['previous_source_data'],{'original':'Preserve evidence'})

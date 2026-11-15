from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import tempfile
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook
from pypdf import PdfReader
from .models import Employee,Training,WorkforceReference,ExecutiveReport
from .executive import ExecutiveFilters,selection,analyse
from .analytics import statistics
from .kpi_service import Scope
from .aggregate_privacy import restricted

class GovernanceTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('governance',password='test');self.client.force_login(self.user)
        self.people=[Employee.objects.create(number=f'G{i}',name=f'Synthetic {i}',direction='Ops',category='Tech',gender='F') for i in range(10)]
        self.rows=[Training.objects.create(employee=e,title='Course',start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1,attendance_confirmed=True) for e in self.people]
        self.ref=WorkforceReference.objects.create(start=date(2025,1,1),end=date(2025,1,31),headcount=20,responsible='DRH',source='Approval 1',actor='admin',approved=True)
    def test_exact_reference_changes_only_coverage_and_never_sums_months(self):
        scope=Scope({'reference':self.ref},'January','training_start','current_registry',self.ref.start,self.ref.end)
        stats=statistics(self.rows,self.people,scope);m=stats['kpi_summary'].metrics['coverage']
        self.assertEqual(m.value,50);self.assertEqual(m.denominator,20);self.assertIn('DRH #',stats['kpi_summary'].scope.reference)
        self.assertEqual(stats['hours'],70)
        annual=statistics(self.rows,self.people,Scope({'reference':self.ref},'Annual','training_start','current_registry',date(2025,1,1),date(2025,12,31)))
        self.assertEqual(annual['kpi_summary'].metrics['coverage'].value,100)
        self.assertEqual(annual['kpi_summary'].scope.reference,'current_registry')
    def test_form_rejects_mismatch_dimensions_period_or_unapproved(self):
        base=dict(period='month',year='2025',month=1,reference=self.ref.pk)
        self.assertTrue(ExecutiveFilters(base).is_valid())
        for change in [dict(month=2),dict(direction='Ops'),dict(category='Tech'),dict(gender='F')]:
            form=ExecutiveFilters({**base,**change});self.assertFalse(form.is_valid());self.assertIn('reference',form.errors)
        self.ref.source='rewrite'
        with self.assertRaises(ValidationError):self.ref.save()
    def test_zero_denominator_and_over_100_are_explicit(self):
        scope=lambda r:Scope({'reference':r},'Jan','training_start','current_registry',r.start,r.end)
        for count,status,value in [(0,'inconsistent',None),(5,'inconsistent',Decimal('200'))]:
            ref=WorkforceReference.objects.create(start=self.ref.start,end=self.ref.end,headcount=count,responsible='DRH',source='Test',actor='admin',approved=True)
            metric=statistics(self.rows,self.people,scope(ref))['kpi_summary'].metrics['coverage']
            self.assertEqual((metric.status,metric.value),(status,value))
    def test_small_partition_and_complement_suppress_all_formats(self):
        Employee.objects.filter(pk=self.people[0].pk).update(direction='Private unit')
        for route in ('dashboard','participation_executive','indicator_index','executive_reports'):
            response=self.client.get(reverse(route));self.assertEqual(response.status_code,200)
            self.assertContains(response,'&lt;5');self.assertNotContains(response,'Synthetic 0')
        csv=self.client.get(reverse('dashboard'),{'export':'csv'}).content.decode()
        self.assertIn('<5',csv);self.assertNotIn('70',csv)
        xlsx=self.client.get(reverse('participation_executive'),{'export':'xlsx'})
        book=load_workbook(BytesIO(xlsx.content));self.assertEqual(book.sheetnames,['Protecção']);self.assertIn('<5',book.active['A1'].value)
        pdf=self.client.get(reverse('executive_preview'));content=b''.join(pdf.streaming_content)
        text=''.join(p.extract_text() for p in PdfReader(BytesIO(content)).pages)
        self.assertIn('<5',text);self.assertNotIn('Synthetic',text)
        # Releasing the other nine would disclose the excluded singleton by subtraction.
        self.assertTrue(restricted(self.rows[1:],self.people[1:]))
    def test_large_scope_exports_remain_available(self):
        self.assertFalse(restricted(self.rows,self.people))
        response=self.client.get(reverse('dashboard'),{'export':'csv'})
        self.assertIn('Horas frequentadas',response.content.decode())
        response=self.client.get(reverse('participation_executive'),{'export':'xlsx'})
        self.assertIn('Critérios e indicadores',load_workbook(BytesIO(response.content)).sheetnames)
    def test_immutable_versions_require_reason_and_same_scope(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(DATA_DIR=Path(folder)):
            data=dict(period='month',year='2025',month=1,time_basis='training_start')
            first=self.client.post(reverse('executive_create'),data);self.assertEqual(first.status_code,200)
            content=b''.join(first.streaming_content);first.close();old=ExecutiveReport.objects.get()
            payload={**data,'predecessor':str(old.pk)}
            self.assertEqual(self.client.post(reverse('executive_create'),payload).status_code,400)
            self.assertEqual(self.client.post(reverse('executive_create'),{**payload,'month':2,'correction_reason':'Correction'}).status_code,400)
            Training.objects.filter(pk=self.rows[0].pk).update(attendance=0)
            new=self.client.post(reverse('executive_create'),{**payload,'correction_reason':'Presence corrected'});self.assertEqual(new.status_code,200);new.close()
            updated=ExecutiveReport.objects.get(predecessor=old)
            self.assertEqual(updated.revision,2);self.assertEqual(updated.summary['hours'],'63.00')
            self.assertEqual((Path(folder)/'reports'/f'{old.pk}.pdf').read_bytes(),content)
            self.assertEqual(self.client.post(reverse('executive_create'),{**payload,'correction_reason':'Stale'}).status_code,409)
            self.assertIn('certification',updated.summary)
    def test_nominal_drill_does_not_grant_new_access(self):
        self.user.is_superuser=False;self.user.is_staff=False;self.user.save()
        self.assertEqual(self.client.get(reverse('executive_explore')).status_code,403)
        self.assertEqual(self.client.get(reverse('workforce_references')).status_code,403)

    def test_incomplete_registry_is_counted_and_zero_is_not_suppressed(self):
        for person in self.people:
            person.category='';person.save()
        stats=statistics(self.rows,self.people)
        self.assertEqual(stats['kpi_summary'].incomplete_registry['category'],10)
        self.assertFalse(restricted([],[]))

    def test_source_changes_flag_archive_and_reference_can_be_corrected(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(DATA_DIR=Path(folder)):
            data=dict(period='month',year='2025',month=1,time_basis='training_start',reference=self.ref.pk)
            response=self.client.post(reverse('executive_create'),data);self.assertEqual(response.status_code,200);response.close()
            old=ExecutiveReport.objects.get()
            self.assertFalse(self.client.get(reverse('executive_reports')).context['archive_page'][0].needs_review)
            newref=WorkforceReference.objects.create(start=self.ref.start,end=self.ref.end,headcount=25,responsible='DRH',source='Corrected approval',actor='admin',approved=True)
            self.assertTrue(self.client.get(reverse('executive_reports')).context['archive_page'][0].needs_review)
            response=self.client.post(reverse('executive_create'),{**data,'reference':newref.pk,'predecessor':old.pk,'correction_reason':'Correct DRH denominator'})
            self.assertEqual(response.status_code,200);response.close()
            report=ExecutiveReport.objects.get(predecessor=old)
            self.assertEqual(report.summary['kpi']['metrics']['coverage']['denominator'],25)
            old.refresh_from_db();self.assertEqual(old.summary['kpi']['metrics']['coverage']['denominator'],20)

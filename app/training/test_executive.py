from datetime import date
from decimal import Decimal
import hashlib, io, sqlite3, tempfile, uuid, zipfile
from pathlib import Path
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Employee, Training, ExecutiveReport
from .executive import bounds, previous_bounds, selection, analyse


class ExecutiveCentreTests(TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.settings_override=override_settings(DATA_DIR=Path(self.folder.name));self.settings_override.enable();self.addCleanup(self.settings_override.disable)
        self.user=get_user_model().objects.create_user('director',password='test',is_staff=True)
        self.client.force_login(self.user)
        self.person=Employee.objects.create(number='9336',name='Técnico <EPAL>',direction='Operações')
        self.old=Training.objects.create(employee=self.person,title='Formação anterior',area='Técnica',start=date(2025,12,20),end=date(2026,1,5),days=2,hours_per_day=7,attendance=2,grade=0,result='Reprovado',certified=True,certificate_delivery_date=date(2026,2,1))
        self.new=Training.objects.create(employee=self.person,title='Formação actual',area='Gestão',start=date(2026,1,10),end=date(2026,1,11),days=2,hours_per_day=7,attendance=1,grade=16,result='Aprovado',certified=None)

    def test_period_boundaries_calendar_and_previous(self):
        self.assertEqual(bounds({'period':'month','year':'2024','month':2})[:2],(date(2024,2,1),date(2024,2,29)))
        self.assertEqual(bounds({'period':'quarter','year':'2026','quarter':4})[:2],(date(2026,10,1),date(2026,12,31)))
        self.assertEqual(bounds({'period':'semester','year':'2026','semester':2})[:2],(date(2026,7,1),date(2026,12,31)))
        self.assertEqual(previous_bounds(date(2026,1,1),date(2026,3,31),'quarter'),(date(2025,10,1),date(2025,12,31)))
        self.assertEqual(previous_bounds(date(2026,3,5),date(2026,3,9),'custom'),(date(2026,2,28),date(2026,3,4)))

    def test_dated_deliveries_are_not_misassigned_to_start_month(self):
        response=self.client.get(reverse('dashboard'),{'period':'month','year':'2026','month':'2'})
        self.assertFalse(response.context['has_rows'])
        self.assertEqual(response.context['stats']['hours'],0)
        self.assertEqual(len(response.context['deliveries']),1)
        self.assertEqual(response.context['deliveries'][0].pk,self.old.pk)
        detail=self.client.get(reverse('executive_explore'),{'period':'month','year':'2026','month':'2','slice':'deliveries','fragment':'1'})
        self.assertContains(detail,'Formação anterior')
        self.assertNotContains(detail,'Formação actual')

    def test_zero_grade_is_included_and_missing_values_are_not(self):
        response=self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['average_final'],8)
        self.assertEqual(response.context['kpis'][7]['value'],50)

    def test_click_filters_preserve_period_and_direction(self):
        response=self.client.get(reverse('executive_explore'),{'year':'2026','direction':'Operações','slice':'area','value':'Técnica'})
        self.assertEqual(response.context['detail_count'],0)
        response=self.client.get(reverse('executive_explore'),{'year':'2026','slice':'time','value':'2026-01-10'})
        self.assertEqual(response.context['detail_count'],1)
        self.assertContains(response,'Técnico &lt;EPAL&gt;')
        self.assertEqual(self.client.get(reverse('executive_explore'),{'slice':'unknown'}).status_code,404)

    def test_invalid_report_filters_do_not_create_an_archive(self):
        response=self.client.post(reverse('executive_create'),{'period':'month','year':'2026'})
        self.assertEqual(response.status_code,400)
        self.assertFalse(ExecutiveReport.objects.exists())
        response=self.client.get(reverse('executive_preview'),{'period':'custom','date_from':'2026-03-10','date_to':'2026-02-01'})
        self.assertEqual(response.status_code,400)

    def test_pdf_snapshot_is_immutable_audited_and_integrity_checked(self):
        response=self.client.post(reverse('executive_create'),{'period':'quarter','year':'2026','quarter':'1','direction':'Operações'})
        self.assertEqual(response.status_code,200)
        content=b''.join(response.streaming_content);response.close()
        self.assertTrue(content.startswith(b'%PDF-'))
        report=ExecutiveReport.objects.get()
        self.assertEqual(report.summary['hours'],'7.00')
        self.assertEqual(report.summary['dated_deliveries'],1)
        self.assertEqual(report.sha256,hashlib.sha256(content).hexdigest())
        self.new.attendance=2;self.new.save()
        download=self.client.get(reverse('executive_download',args=[report.pk]));self.assertEqual(b''.join(download.streaming_content),content);download.close()
        path=Path(self.folder.name)/'reports'/f'{report.pk}.pdf';path.write_bytes(b'changed')
        self.assertEqual(self.client.get(reverse('executive_download',args=[report.pk])).status_code,409)

    def test_pdf_has_logo_graphics_footer_and_total_pages(self):
        from .executive_views import context
        from .executive_pdf import render_report
        try:
            import pypdf
        except ImportError:
            self.skipTest('Leitura estrutural do PDF requer pypdf no ambiente de testes.')
        request=SimpleNamespace(GET={'year':'2026'})
        data,valid=context(request);pdf=render_report(data,actor='Teste')
        reader=pypdf.PdfReader(io.BytesIO(pdf));text='\n'.join(p.extract_text() for p in reader.pages)
        self.assertIn('Síntese executiva',text)
        self.assertIn('Formação actual',text)
        self.assertIn('Formação anterior',text) # Dated certificate delivery is included separately.
        self.assertIn(f'Pág. 1/{len(reader.pages)}',text)
        self.assertTrue(any('/XObject' in p['/Resources'] for p in reader.pages))
        for page in reader.pages:self.assertIn('RELATÓRIO EXECUTIVO DE FORMAÇÃO',page.extract_text())

    def test_archive_creation_requires_staff_and_post_and_csrf(self):
        self.assertEqual(self.client.get(reverse('executive_create')).status_code,405)
        self.user.is_staff=False;self.user.save()
        self.assertEqual(self.client.post(reverse('executive_create')).status_code,403)
        self.user.is_staff=True;self.user.save()
        from django.test import Client
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.user)
        self.assertEqual(strict.post(reverse('executive_create'),{'year':'2026'}).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('executive_reports')).status_code,302)

    def test_backup_carries_database_and_report_files(self):
        self.user.is_superuser=True; self.user.save(update_fields=["is_superuser"])
        identifier=uuid.uuid4();path=Path(self.folder.name)/'reports';path.mkdir();(path/f'{identifier}.pdf').write_bytes(b'%PDF-demo')
        database=Path(self.folder.name)/'base.sqlite3'
        with sqlite3.connect(database) as c:
            c.execute('CREATE TABLE training_executivereport (id TEXT)');c.execute('INSERT INTO training_executivereport VALUES (?)',(identifier.hex,))
        with override_settings(DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':database}}):
            response=self.client.post(reverse('backup'));self.assertEqual(response.status_code,200)
            payload=b''.join(response.streaming_content);response.close()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read(f'reports/{identifier}.pdf'),b'%PDF-demo')
            self.assertTrue(archive.read('pmf.sqlite3').startswith(b'SQLite format 3'))

    def test_unconfirmed_area_chart_drills_into_every_grouped_row(self):
        self.old.area='';self.old.save();self.new.area='Área por confirmar';self.new.save()
        response=self.client.get(reverse('executive_explore'),{'slice':'area','value':'__unconfirmed__'})
        self.assertEqual(response.context['detail_count'],2)

    def test_failed_archive_write_leaves_no_orphan_pdf(self):
        from unittest.mock import patch
        with patch('training.executive_views.ExecutiveReport.objects.create',side_effect=RuntimeError('database')):
            with self.assertRaises(RuntimeError):self.client.post(reverse('executive_create'),{'year':'2026'})
        self.assertFalse(ExecutiveReport.objects.exists())
        self.assertEqual(list((Path(self.folder.name)/'reports').glob('*')),[])

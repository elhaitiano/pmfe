from datetime import date, timedelta
import hashlib
import io
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import zipfile
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .analytics import statistics
from .documents import CertificateForm, document_path
from .management.commands.restore_backup import restore
from .models import Employee, Training, PrivateDocument, AuditEvent, ExecutiveReport

PDF = b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n'


def create_training(employee):
    return Training.objects.create(employee=employee, title='Curso teste', start=date(2025,1,1),
        end=date(2025,1,2), days=2, hours_per_day=7, attendance=2, status='Concluído')


class DocumentTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.override = override_settings(DATA_DIR=Path(self.folder.name))
        self.override.enable(); self.addCleanup(self.override.disable)
        self.staff = get_user_model().objects.create_user('doc-admin', is_staff=True)
        self.reader = get_user_model().objects.create_user('doc-reader')
        self.employee = Employee.objects.create(number='D1', name='Teste')
        self.training = create_training(self.employee)
        self.client.force_login(self.staff)
        self.url = reverse('employee_documents', args=[self.employee.pk])

    def upload(self, **changes):
        data = {'kind': 'cv', 'file': SimpleUploadedFile('curriculo.pdf', PDF, content_type='application/pdf')}
        data.update(changes)
        return self.client.post(self.url, data)

    def test_upload_private_download_hash_and_archival(self):
        self.assertEqual(self.upload().status_code, 302)
        doc = PrivateDocument.objects.get()
        self.assertEqual(document_path(doc.pk).read_bytes(), PDF)
        self.assertEqual(doc.sha256, hashlib.sha256(PDF).hexdigest())
        response = self.client.get(reverse('document_download', args=[doc.pk]))
        self.assertEqual(b''.join(response.streaming_content), PDF); response.close()
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.client.post(reverse('document_archive', args=[doc.pk]), {'action':'archive','reason':'Versão antiga'})
        doc.refresh_from_db(); self.assertFalse(doc.active)
        self.assertTrue(document_path(doc.pk).is_file())
        self.client.post(reverse('document_archive', args=[doc.pk]), {'action':'restore','reason':'Reactivar'})
        doc.refresh_from_db(); self.assertTrue(doc.active)
        document_path(doc.pk).write_bytes(b'changed')
        self.assertEqual(self.client.get(reverse('document_download', args=[doc.pk])).status_code, 409)
        self.assertTrue(AuditEvent.objects.filter(action='ADD_PRIVATE_DOCUMENT').exists())

    def test_access_and_csrf(self):
        self.upload(); doc = PrivateDocument.objects.get()
        urls = [self.url, reverse('document_download', args=[doc.pk]), reverse('certificate_edit', args=[self.training.pk])]
        self.client.force_login(self.reader)
        for url in urls: self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.upload().status_code, 403)
        self.client.logout()
        for url in urls: self.assertEqual(self.client.get(url).status_code, 302)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.staff)
        self.assertEqual(strict.post(self.url, {}).status_code, 403)
        self.assertEqual(strict.post(reverse('document_archive',args=[doc.pk]), {}).status_code, 403)
        self.assertEqual(strict.post(reverse('certificate_edit',args=[self.training.pk]), {}).status_code, 403)

    def test_reject_non_pdf_oversize_and_foreign_participation(self):
        self.assertEqual(self.upload(file=SimpleUploadedFile('fake.pdf', b'not pdf')).status_code, 200)
        with patch('training.documents.MAX_PDF_SIZE', 5):
            self.assertEqual(self.upload().status_code, 200)
        other = Employee.objects.create(number='D2', name='Outro')
        foreign = create_training(other)
        response = self.upload(kind='certificate', training=foreign.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PrivateDocument.objects.exists())
        self.assertEqual(self.upload(kind='certificate', training=self.training.pk).status_code, 302)
        self.training.refresh_from_db()
        self.assertIsNone(self.training.certified)
        self.assertIsNone(self.training.certificate_issued)
        response = self.client.post(reverse('training_delete', args=[self.training.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Training.objects.filter(pk=self.training.pk).exists())

    def test_failed_audit_rolls_back_file_and_database(self):
        with patch('training.documents.record', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError): self.upload()
        self.assertFalse(PrivateDocument.objects.exists())
        self.assertEqual(list((Path(self.folder.name)/'documents').glob('*.pdf')), [])

    def certificate_data(self, **changes):
        data = dict(revision=self.training.version, certificate_issued='unknown', certified='unknown',
            certificate_hr_delivered='unknown', certificate_declaration='Emitido e entregue segundo o responsável',
            certificate_declared_by='Responsável de teste', certificate_declaration_source='Declaração verbal')
        data.update(changes); return data

    def test_declaration_does_not_invent_delivery_or_issue_dates_and_stale_edit_blocked(self):
        url = reverse('certificate_edit', args=[self.training.pk])
        data = self.certificate_data()
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.training.refresh_from_db()
        self.assertIsNone(self.training.certified)
        self.assertIsNone(self.training.certificate_delivery_date)
        self.assertIsNone(self.training.certificate_issue_date)
        counts = statistics([self.training])
        self.assertEqual(counts['certificate_declarations'], 1)
        self.assertEqual(counts['certificates'], 0)
        data['certificate_declaration'] = 'Alteração antiga'
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.training.refresh_from_db()
        self.assertNotEqual(self.training.certificate_declaration, 'Alteração antiga')

    def test_distinct_deliveries_and_chronology(self):
        data = self.certificate_data(certificate_issued='true', certificate_issue_date='2025-01-03',
            certified='true', certificate_delivery_date='2025-01-04',
            certificate_hr_delivered='true', certificate_hr_delivery_date='2025-01-05')
        form = CertificateForm(instance=self.training, data=data)
        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertEqual(row.certificate_delivery_date, date(2025,1,4))
        self.assertEqual(row.certificate_hr_delivery_date, date(2025,1,5))
        for changes in [dict(certificate_hr_delivery_date=''), dict(certificate_hr_delivery_date='2025-01-01'),
                        dict(certificate_issue_date=(timezone.localdate()+timedelta(days=1)).isoformat()),
                        dict(certificate_issued='false'), dict(certificate_declared_by='')]:
            row.refresh_from_db()
            form = CertificateForm(instance=row, data={**data, **changes})
            self.assertFalse(form.is_valid(), changes)


class DocumentRecoveryTests(TransactionTestCase):
    def test_export_restore_archived_document_and_reject_corruption_or_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            employee = Employee.objects.create(number='R1', name='Recuperação')
            doc = PrivateDocument.objects.create(employee=employee, kind='cv', original_name='cv.pdf',
                sha256=hashlib.sha256(PDF).hexdigest(), size=len(PDF), actor='Teste', active=False)
            (folder/'documents').mkdir(); (folder/'documents'/f'{doc.pk}.pdf').write_bytes(PDF)
            report = ExecutiveReport.objects.create(actor='Teste', title='Relatório', filters={}, summary={}, sha256=hashlib.sha256(PDF).hexdigest())
            (folder/'reports').mkdir(); (folder/'reports'/f'{report.pk}.pdf').write_bytes(PDF)
            (folder/'secret.key').write_text('test-only-key')
            user = get_user_model().objects.create_user('recovery-admin', is_staff=True)
            self.client.force_login(user)
            from .models import TrainingEvaluation, ReactionEvaluation, Exam, ExamQuestion, ExamAttempt, ExamAnswer
            participation = Training.objects.create(employee=employee, title='Prova de recuperação',
                start=date(2026,1,1), end=date(2026,1,1), days=1, hours_per_day=7, attendance=1)
            TrainingEvaluation.objects.create(training=participation, logistics=4)
            ReactionEvaluation.objects.create(training=participation, organization=5)
            exam = Exam.objects.create(training=participation, title='Prova')
            question = ExamQuestion.objects.create(exam=exam, number=1, prompt='P', expected_answer='R', points=10)
            attempt = ExamAttempt.objects.create(exam=exam, number=1, authorized_by='Teste')
            ExamAnswer.objects.create(attempt=attempt, question=question, response='Resposta preservada', awarded=8)
            snapshot = folder/'source.sqlite3'
            with sqlite3.connect(snapshot) as target: connection.connection.backup(target)
            config = SimpleNamespace(DATA_DIR=folder, DATABASES={'default': {'NAME': snapshot}})
            with patch('training.views.settings', config):
                response = self.client.post(reverse('backup'))
            self.assertEqual(response.status_code, 200)
            archive = folder/'copy.zip'; archive.write_bytes(b''.join(response.streaming_content)); response.close()
            destination = restore(archive, folder/'restored')
            self.assertEqual((destination/'documents'/f'{doc.pk}.pdf').read_bytes(), PDF)
            self.assertEqual((destination/'reports'/f'{report.pk}.pdf').read_bytes(), PDF)
            self.assertEqual((destination/'secret.key').read_text(), 'test-only-key')
            with sqlite3.connect(destination/'pmf.sqlite3') as db:
                self.assertEqual(db.execute('SELECT active FROM training_privatedocument').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT response, awarded FROM training_examanswer').fetchone(), ('Resposta preservada', 8))
                self.assertEqual(db.execute('SELECT logistics FROM training_trainingevaluation').fetchone()[0], 4)
                self.assertEqual(db.execute('SELECT organization FROM training_reactionevaluation').fetchone()[0], 5)
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            with self.assertRaises(ValueError): restore(archive, destination)
            bad = folder/'bad.zip'
            with zipfile.ZipFile(archive) as original, zipfile.ZipFile(bad,'w') as output:
                for entry in original.infolist():
                    output.writestr(entry.filename, b'bad' if entry.filename.startswith('documents/') else original.read(entry))
            with self.assertRaises(ValueError): restore(bad, folder/'bad-destination')
            self.assertFalse((folder/'bad-destination').exists())
            with zipfile.ZipFile(bad, 'w') as output: output.writestr('../outside', b'bad')
            with self.assertRaises(ValueError): restore(bad, folder/'bad-destination')
            (folder/'documents'/f'{doc.pk}.pdf').unlink()
            with patch('training.views.settings', config):
                self.assertEqual(self.client.post(reverse('backup')).status_code, 409)


class CertificateMigrationTests(TransactionTestCase):
    def test_source_declaration_is_preserved_without_inferring_delivery(self):
        before = [('training','0015_training_history_integrity')]
        after = [('training','0016_certificates_private_documents')]
        executor=MigrationExecutor(connection); executor.migrate(before)
        try:
            apps=executor.loader.project_state(before).apps
            person=apps.get_model('training','Employee').objects.create(number='C1',name='Teste')
            source={'Estado certificado — declaração':'Emitido e entregue', 'Origem da confirmação':'Declaração sem data documental'}
            apps.get_model('training','Training').objects.create(employee=person,title='Curso',start=date(2025,1,1),
                end=date(2025,1,1),days=1,hours_per_day=7,attendance=1,source_data=source)
            executor=MigrationExecutor(connection); executor.migrate(after)
            row=executor.loader.project_state(after).apps.get_model('training','Training').objects.get()
            self.assertEqual(row.certificate_declaration,'Emitido e entregue')
            self.assertEqual(row.source_data,source)
            self.assertIsNone(row.certified)
            self.assertIsNone(row.certificate_issued)
            self.assertIsNone(row.certificate_delivery_date)
            self.assertEqual(row.certificate_declared_by,'')
            row.certificate_issued = True
            row.certificate_issue_date = date(2025,1,3)
            row.certificate_declared_by = 'Responsável'
            row.save()
            executor=MigrationExecutor(connection); executor.migrate(before)
            executor=MigrationExecutor(connection); executor.migrate(after)
            restored=executor.loader.project_state(after).apps.get_model('training','Training').objects.get()
            self.assertTrue(restored.certificate_issued)
            self.assertEqual(restored.certificate_issue_date, date(2025,1,3))
            self.assertEqual(restored.certificate_declared_by, 'Responsável')
        finally:
            executor=MigrationExecutor(connection); executor.migrate(executor.loader.graph.leaf_nodes())

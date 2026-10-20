import io
import hashlib
from datetime import date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import model_to_dict
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.urls import reverse
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader, PdfWriter
from .models import Employee, Training, PrivateDocument, TrainingDocumentState, AuditEvent
from .forms import EmployeeForm, TrainingForm
from . import documents as service


def pdf_bytes():
    stream = io.BytesIO();canvas = Canvas(stream);canvas.drawString(50, 700, 'Synthetic PDF only');canvas.save()
    return stream.getvalue()


@override_settings(PMF_DOCUMENTS_ENABLED=True)
class DocumentTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('archive', password='synthetic')
        self.reader = get_user_model().objects.create_user('reader')
        self.staff = get_user_model().objects.create_user('staff', is_staff=True)
        self.employee = Employee.objects.create(number='SYN', name='Synthetic', category='Categoria A', function='Função B',
            phone='000000201', source_data={'Telefone 2':'', 'Função':'Fonte original'})
        self.other = Employee.objects.create(number='OTHER', name='Other synthetic')
        self.training = Training.objects.create(employee=self.employee, title='Synthetic training', start=date(2025,1,1),
            end=date(2025,1,2), days=2, hours_per_day=7, attendance=2, attendance_confirmed=True)
        self.history = model_to_dict(self.training)
        self.client.force_login(self.admin)

    def upload_form(self, **overrides):
        data = {'kind':'certificate','training':self.training.pk,'reference':'Synthetic source'}
        data.update(overrides)
        return service.UploadForm(data, {'pdf':SimpleUploadedFile('certificate.pdf',pdf_bytes(),content_type='application/pdf')},employee=self.employee)

    def delivery_data(self, **overrides):
        data = {'revision':0,'issued':'true','learner_delivered':'true','learner_date':'2025-01-03',
            'learner_responsible':'Synthetic responsible','hr_delivered':'unknown','reference':'Synthetic list',
            'justification':'Synthetic documented decision','confirm':'on'}
        # Django NullBooleanSelect uses unknown/true/false, no automatic default.
        data.update(overrides);return data

    def assert_history(self):
        self.training.refresh_from_db();self.assertEqual(model_to_dict(self.training),self.history)

    def test_valid_upload_audited_without_changing_history_or_delivery(self):
        form=self.upload_form();self.assertTrue(form.is_valid(),form.errors)
        document=service.store_document(self.admin,self.employee,form)
        self.assertEqual(document.sha256,hashlib.sha256(bytes(document.content)).hexdigest())
        self.assertEqual(AuditEvent.objects.get().action,'PRIVATE_DOCUMENT_UPLOAD')
        self.assertFalse(TrainingDocumentState.objects.exists());self.assert_history()

    def test_file_type_size_and_structure_rejected(self):
        for name,ctype,content in [('x.txt','application/pdf',pdf_bytes()),('x.pdf','text/plain',pdf_bytes()),
            ('x.pdf','application/pdf',b'%PDF-1.4 not a document %%EOF'),('x.pdf','application/pdf',b'x'*(service.MAX_PDF_BYTES+1))]:
            with self.subTest(name=name,ctype=ctype),self.assertRaises(ValidationError):
                service.validate_pdf(SimpleUploadedFile(name,content,content_type=ctype))

    def test_active_and_encrypted_pdfs_rejected(self):
        for active in (True,False):
            writer=PdfWriter();writer.add_blank_page(width=100,height=100)
            if active:writer.add_js('app.alert("not allowed")')
            else:writer.encrypt('secret')
            stream=io.BytesIO();writer.write(stream)
            with self.assertRaises(ValidationError):service.validate_pdf(SimpleUploadedFile('x.pdf',stream.getvalue(),content_type='application/pdf'))

    def test_cross_employee_and_cv_training_rejected(self):
        training=Training.objects.create(employee=self.other,title='Other',start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1)
        self.assertFalse(self.upload_form(training=training.pk).is_valid())
        self.assertFalse(self.upload_form(kind='cv').is_valid())
        form=self.upload_form(kind='cv',training='');self.assertTrue(form.is_valid(),form.errors)
        self.assertIsNone(service.store_document(self.admin,self.employee,form).training_id)

    def test_permissions_anonymous_reader_staff_and_granted_staff(self):
        url=reverse('document_upload',args=[self.employee.pk]);self.client.logout()
        self.assertEqual(self.client.get(url).status_code,302)
        for user in (self.reader,self.staff):
            self.client.force_login(user);self.assertEqual(self.client.get(url).status_code,403)
            with self.assertRaises(PermissionDenied):service.store_document(user,self.employee,self.upload_form())
        self.staff.user_permissions.add(Permission.objects.get(codename='add_privatedocument'))
        self.client.force_login(self.staff);self.assertEqual(self.client.get(url).status_code,200)

    def test_upload_csrf_and_post_flow_without_javascript(self):
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.admin)
        url=reverse('document_upload',args=[self.employee.pk]);self.assertEqual(strict.post(url,{}).status_code,403)
        response=self.client.post(url,{'kind':'cv','reference':'CV provided','pdf':SimpleUploadedFile('cv.pdf',pdf_bytes(),content_type='application/pdf')})
        self.assertEqual(response.status_code,302);self.assertEqual(PrivateDocument.objects.count(),1)

    def test_private_download_permissions_headers_and_integrity(self):
        document=service.store_document(self.admin,self.employee,self.upload_form());url=reverse('document_download',args=[document.pk])
        self.client.force_login(self.reader);self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.admin)
        response=self.client.get(url);self.assertIn('attachment',response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'],'private, no-store');self.assertEqual(response['X-Content-Type-Options'],'nosniff')
        self.assertEqual(b''.join(response.streaming_content),bytes(document.content));response.close()
        response=self.client.get(url+'?view=1');self.assertIn('inline',response['Content-Disposition']);response.close()
        PrivateDocument.objects.filter(pk=document.pk).update(content=b'corrupt')
        self.assertEqual(self.client.get(url).status_code,409)

    def test_upload_audit_failure_rolls_back(self):
        with patch('training.documents.audit',side_effect=RuntimeError('synthetic audit failure')),self.assertRaises(RuntimeError):
            service.store_document(self.admin,self.employee,self.upload_form())
        self.assertFalse(PrivateDocument.objects.exists())

    def test_delivery_separate_states_audit_and_history(self):
        state=service.save_delivery(self.admin,self.training,self.delivery_data())
        self.assertTrue(state.issued);self.assertTrue(state.learner_delivered);self.assertIsNone(state.hr_delivered)
        self.assertEqual(AuditEvent.objects.get().action,'DOCUMENT_STATE_CHANGE');self.assert_history()
        summary=service.delivery_summary(self.training);self.assertEqual(summary['issued'],'Sim');self.assertEqual(summary['hr'],'Por confirmar')
        self.assertEqual(summary['grade_status'],'Por confirmar');self.assertEqual(summary['participation'],'Comprovada')

    def test_delivery_validation_and_stale_repeat_rollback(self):
        for overrides in ({'learner_date':''},{'issued':'unknown'},{'learner_responsible':''},{'hr_delivered':'true'},
            {'learner_date':'2099-01-01'},{'learner_date':'2024-01-01'},{'reference':''},{'confirm':''}):
            with self.subTest(overrides=overrides),self.assertRaises(ValidationError):service.save_delivery(self.admin,self.training,self.delivery_data(**overrides))
        self.assertEqual(AuditEvent.objects.count(),0)
        service.save_delivery(self.admin,self.training,self.delivery_data())
        with self.assertRaises(ValidationError):service.save_delivery(self.admin,self.training,self.delivery_data())
        self.assertEqual(AuditEvent.objects.count(),1);self.assertEqual(TrainingDocumentState.objects.count(),1)

    def test_delivery_permissions_direct_requests_and_audit_failure(self):
        self.client.force_login(self.staff);url=reverse('document_delivery',args=[self.training.pk])
        self.assertEqual(self.client.post(url,self.delivery_data()).status_code,403)
        original=AuditEvent.save
        def failing(event,*args,**kwargs):
            if event.action=='DOCUMENT_STATE_CHANGE':raise RuntimeError('audit failure')
            return original(event,*args,**kwargs)
        with patch.object(AuditEvent,'save',failing),self.assertRaises(RuntimeError):service.save_delivery(self.admin,self.training,self.delivery_data())
        self.assertFalse(TrainingDocumentState.objects.exists());self.assertFalse(AuditEvent.objects.exists())

    def test_unknown_legacy_states_and_zero_grade_not_inferred(self):
        summary=service.delivery_summary(self.training)
        self.assertEqual(summary['issued'],'Por confirmar');self.assertEqual(summary['learner'],'Por confirmar')
        self.training.grade=0;self.training.attendance_confirmed=False
        summary=service.delivery_summary(self.training);self.assertEqual(summary['grade'],0);self.assertEqual(summary['participation'],'Por confirmar')

    def test_professional_correction_preserves_source_and_is_audited(self):
        original=dict(self.employee.source_data)
        data={'category':'New category','function':'Distinct function','revision':service.professional_revision(self.employee),
            'justification':'Documented local correction','reference':'Synthetic document','confirm':'on'}
        service.correct_professional(self.admin,self.employee.pk,data);self.employee.refresh_from_db()
        self.assertEqual(self.employee.source_data,original);self.assertEqual(self.employee.function,'Distinct function')
        self.assertEqual(AuditEvent.objects.get().action,'PROFESSIONAL_CORRECTION')
        with self.assertRaises(ValidationError):service.correct_professional(self.admin,self.employee.pk,data)
        self.assertEqual(AuditEvent.objects.count(),1);self.assert_history()

    def test_normal_edit_cannot_bypass_professional_correction(self):
        form=EmployeeForm({'number':'SYN','name':'Synthetic','category':'Forged','function':'Forged','phone':'000000201'},instance=self.employee)
        self.assertTrue(form.is_valid(),form.errors);form.save();self.employee.refresh_from_db()
        self.assertEqual(self.employee.category,'Categoria A');self.assertEqual(self.employee.function,'Função B')
        self.assertEqual(self.employee.source_data['Função'],'Fonte original')

    def test_contact_validation_and_two_numbers(self):
        for field in ('phone','phone_secondary'):
            form=EmployeeForm({'number':'SYN','name':'Synthetic',field:'not a phone'},instance=Employee.objects.get(pk=self.employee.pk))
            self.assertFalse(form.is_valid());self.assertIn(field,form.errors)
        form=EmployeeForm({'number':'SYN','name':'Synthetic','phone':'000000201','phone_secondary':'+244 924 123 456'},instance=Employee.objects.get(pk=self.employee.pk))
        self.assertTrue(form.is_valid(),form.errors);form.save();self.employee.refresh_from_db()
        self.assertEqual(self.employee.phone,'000000201');self.assertEqual(self.employee.source_data['Telefone 2'],'+244 924 123 456')
        response=self.client.get(reverse('employee_data',args=[self.employee.pk]));self.assertContains(response,'+244 924 123 456')

    def test_profile_and_disabled_activation_safe(self):
        response=self.client.get(reverse('employee_data',args=[self.employee.pk]))
        self.assertContains(response,'ARQUIVO DIGITAL PRIVADO');self.assertContains(response,'Função B')
        self.assertContains(response,'Categoria A')
        self.assertContains(response,'<span class="ficha-contact-label">Tel.:</span>',html=True)
        self.assertContains(response,'000 000 201')
        self.assertNotContains(response,'Tel. 2')
        with override_settings(PMF_DOCUMENTS_ENABLED=False):
            response=self.client.get(reverse('employee_data',args=[self.employee.pk]));self.assertNotContains(response,'ARQUIVO DIGITAL PRIVADO')
            self.assertEqual(self.client.get(reverse('document_upload',args=[self.employee.pk])).status_code,403)

    def test_professional_permissions_csrf_and_missing_foundation(self):
        url=reverse('professional_correct',args=[self.employee.pk])
        self.client.force_login(self.staff);self.assertEqual(self.client.post(url,{}).status_code,403)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.admin)
        self.assertEqual(strict.post(url,{}).status_code,403)
        for key in ('reference','justification','confirm'):
            data={'category':'New','function':'Distinct','revision':service.professional_revision(self.employee),
                'reference':'Document','justification':'Reason','confirm':'on'}
            data[key]=''
            with self.assertRaises(ValidationError):service.correct_professional(self.admin,self.employee.pk,data)
        self.assertFalse(AuditEvent.objects.exists())

    def test_professional_audit_failure_rolls_back_and_reverted_values_do_not_allow_replay(self):
        data={'category':'New','function':'Distinct','revision':service.professional_revision(self.employee),
            'reference':'Document','justification':'Reason','confirm':'on'}
        original=AuditEvent.save
        def failing(event,*args,**kwargs):
            if event.action=='PROFESSIONAL_CORRECTION':raise RuntimeError('audit failure')
            return original(event,*args,**kwargs)
        with patch.object(AuditEvent,'save',failing),self.assertRaises(RuntimeError):service.correct_professional(self.admin,self.employee.pk,data)
        self.employee.refresh_from_db();self.assertEqual(self.employee.category,'Categoria A');self.assertFalse(AuditEvent.objects.exists())
        service.correct_professional(self.admin,self.employee.pk,data);self.employee.refresh_from_db()
        service.correct_professional(self.admin,self.employee.pk,dict(data,category='Categoria A',function='Função B',revision=service.professional_revision(self.employee)))
        with self.assertRaises(ValidationError):service.correct_professional(self.admin,self.employee.pk,data)

    def test_pdf_archive_is_not_a_grade_or_issuance_decision(self):
        service.store_document(self.admin,self.employee,self.upload_form())
        summary=service.delivery_summary(self.training)
        self.assertEqual(summary['grade_status'],'Por confirmar');self.assertEqual(summary['issued'],'Por confirmar')
        self.assertEqual(self.training.result,'Pendente');self.assert_history()

    def test_backup_cannot_bypass_private_document_permissions(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse('backup')).status_code,403)
        self.assertFalse(AuditEvent.objects.exists())

    def test_documented_training_cannot_be_deleted(self):
        service.store_document(self.admin,self.employee,self.upload_form())
        response=self.client.post(reverse('training_delete',args=[self.training.pk]))
        self.assertEqual(response.status_code,302)
        self.assertTrue(Training.objects.filter(pk=self.training.pk).exists());self.assert_history()

    def test_issuance_cannot_bypass_completion_rules(self):
        Training.objects.filter(pk=self.training.pk).update(status='Planeado')
        with self.assertRaises(ValidationError):service.save_delivery(self.admin,self.training,self.delivery_data())
        self.assertFalse(TrainingDocumentState.objects.exists());self.assertFalse(AuditEvent.objects.exists())

    def test_uppercase_hours_and_distinct_professional_labels(self):
        response=self.client.get(reverse('employee_data',args=[self.employee.pk]))
        self.assertContains(response,'14,0 H');self.assertContains(response,'Categoria profissional')
        self.assertNotContains(response,'Função exercida:')
        self.assertContains(response,self.employee.function or 'Função por confirmar')
        self.assertContains(response,'Certificados recebidos')
        response=self.client.get(reverse('employee_detail',args=[self.employee.pk]))
        self.assertContains(response,'14,0 H')


@override_settings(PMF_DOCUMENTS_ENABLED=True)
class DocumentConcurrencyTests(TransactionTestCase):
    def test_two_confirmations_commit_one_state_and_one_audit(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections, OperationalError
        user=get_user_model().objects.create_superuser('concurrent',password='synthetic')
        person=Employee.objects.create(number='CON',name='Synthetic')
        training=Training.objects.create(employee=person,title='Synthetic',start=date(2025,1,1),end=date(2025,1,1),days=1,hours_per_day=7,attendance=1)
        data={'revision':0,'issued':'true','learner_delivered':'unknown','hr_delivered':'unknown',
            'reference':'Synthetic evidence','justification':'Synthetic reason','confirm':'on'}
        def run(_):
            close_old_connections()
            try:
                service.save_delivery(get_user_model().objects.get(pk=user.pk),Training.objects.get(pk=training.pk),data)
                return 'saved'
            except (ValidationError,OperationalError):return 'rejected'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
        self.assertEqual(results.count('saved'),1);self.assertEqual(TrainingDocumentState.objects.count(),1)
        self.assertEqual(AuditEvent.objects.count(),1)

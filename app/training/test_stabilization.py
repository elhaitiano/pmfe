from datetime import date
from decimal import Decimal
from django.test import TestCase
from .models import Employee, Training, TrainingEvaluation
from .historical import signature
from .source_achievement import attach
from .batch import BatchForm

class StructuralRegressionTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='SYN-REGRESSION',name='Pessoa sintética')
        self.training=Training.objects.create(employee=self.person,title='Curso sintético',area='Técnica',start=date(2026,1,1),end=date(2026,1,2),days=2,hours_per_day=6,attendance=1,attendance_confirmed=True)

    def test_historical_signature_survives_decimal_days_reload(self):
        original=signature(self.training)
        self.training.refresh_from_db()
        self.assertEqual(self.training.days,Decimal('2'))
        self.assertEqual(signature(self.training),original)

    def test_display_summary_does_not_replace_evaluation_relation(self):
        evaluation=TrainingEvaluation.objects.create(training=self.training)
        attach([self.training])
        self.assertEqual(self.training.evaluation.pk,evaluation.pk)
        self.assertIn('available',self.training.evaluation_summary)

    def test_batch_form_does_not_require_fields_outside_its_scope(self):
        form=BatchForm(instance=self.training)
        self.assertNotIn('area',form.fields)
        self.assertIn('grade',form.fields)

    def test_daily_archive_failure_never_returns_a_successful_partial_day(self):
        from unittest.mock import patch
        from datetime import time
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.core.exceptions import ValidationError
        from .models import Course,Enrollment
        from .planning import create_group
        user=get_user_model().objects.create_superuser('synthetic-archive',password=None)
        course=Course.objects.create(validation_status='validated', title='Curso sintético para arquivo',area='Técnica')
        group=create_group(course,dict(dates=[date(2026,1,1),date(2026,1,2)],trainer='Formador sintético',provider='',location='Sala sintética',capacity=10,hours_per_day=6,break_hours=1,start_time=time(8),end_time=time(16)))
        Enrollment.objects.create(group=group,employee=self.person)
        self.client.force_login(user)
        with patch('training.attendance_excel.daily_archive',side_effect=ValidationError('Falha num dos dias')):
            response=self.client.get(reverse('attendance_excel',args=[group.pk]),{'all':'1'})
        self.assertEqual(response.status_code,400)
        self.assertNotIn('Content-Disposition',response)

    def test_legacy_database_pdf_is_readable_and_permission_checked(self):
        import hashlib,tempfile
        from pathlib import Path
        from django.test import override_settings
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from .models import PrivateDocument
        from .test_documents import pdf_bytes
        content=pdf_bytes()
        document=PrivateDocument.objects.create(employee=self.person,kind='cv',original_name='synthetic.pdf',content=content,size=len(content),sha256=hashlib.sha256(content).hexdigest(),actor='synthetic')
        user=get_user_model().objects.create_superuser('synthetic-document-admin',password=None)
        self.client.force_login(user)
        with tempfile.TemporaryDirectory() as directory,override_settings(DATA_DIR=Path(directory)):
            response=self.client.get(reverse('document_download',args=[document.pk]),{'view':'1'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(b''.join(response.streaming_content),content)
            response.close()
            self.assertIn('sandbox',response['Content-Security-Policy'])
            self.assertIn('inline',response['Content-Disposition'])
            staff=get_user_model().objects.create_user('synthetic-unprivileged-staff',is_staff=True)
            self.client.force_login(staff)
            self.assertEqual(self.client.get(reverse('document_download',args=[document.pk])).status_code,403)

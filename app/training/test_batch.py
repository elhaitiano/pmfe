from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Employee, Training, AuditEvent


class BatchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user('batch-admin', password='test-only', is_staff=True)
        cls.reader = get_user_model().objects.create_user('batch-reader', password='test-only')
        people = [Employee.objects.create(number=f'B{i}', name=f'Formando {i}') for i in range(3)]
        fields = dict(title='Segurança na operação', area='Segurança', trainer='Formador A',
            start=date(2026,1,1), end=date(2026,1,3), days=3, attendance=2,
            hours_per_day=Decimal('7'), status='Concluído', result='Pendente')
        cls.rows = [Training.objects.create(employee=e, **fields) for e in people[:2]]
        cls.outside = Training.objects.create(employee=people[2], **{**fields, 'start':date(2026,2,1), 'end':date(2026,2,3)})

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('batch_edit', args=[self.rows[0].pk])

    def payload(self):
        data = {'participants-TOTAL_FORMS':'2','participants-INITIAL_FORMS':'2',
                'participants-MIN_NUM_FORMS':'0','participants-MAX_NUM_FORMS':'1000','action':'review'}
        for i, row in enumerate(self.rows):
            for field,value in {'id':row.pk,'revision':row.version,'status':'Concluído',
                'attendance':2,'grade':16-i,'result':'Aprovado','certified':'unknown',
                'certificate_delivery_date':'','certificate_reference':''}.items():
                data[f'participants-{i}-{field}'] = str(value)
        return data

    def confirm(self, data):
        review = self.client.post(self.url, data)
        self.assertTrue(review.context['review'], review.context['formset'].errors)
        data = {**data, 'action':'confirm', 'review_token':review.context['review_token']}
        return self.client.post(self.url, data)

    def test_staff_only_and_separate_editions(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.get(reverse('batch_index')).status_code,403)
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['formset'].forms),2)
        self.assertContains(response, 'Formando 0')
        self.assertNotContains(response, 'Formando 2')
        index = self.client.get(reverse('batch_index'))
        self.assertEqual(index.context['page'].paginator.count,2)

    def test_review_does_not_write_and_confirmation_updates_all_with_audit(self):
        data = self.payload()
        response = self.client.post(self.url,data)
        self.assertTrue(response.context['review'])
        self.assertEqual(response.context['modified_count'],2)
        self.assertContains(response,'Confirmar e guardar')
        self.assertFalse(AuditEvent.objects.exists())
        self.rows[0].refresh_from_db();self.assertIsNone(self.rows[0].grade)
        response = self.confirm(data)
        self.assertEqual(response.status_code,302)
        for i,row in enumerate(self.rows):
            row.refresh_from_db();self.assertEqual(row.grade,Decimal(16-i));self.assertEqual(row.version,2)
            self.assertIsNone(row.certified)
        self.assertEqual(AuditEvent.objects.filter(action='BATCH_UPDATE_TRAINING').count(),2)
        self.outside.refresh_from_db();self.assertIsNone(self.outside.grade)

    def test_invalid_row_blocks_entire_batch_and_preserves_entered_values(self):
        data = self.payload();data['participants-1-grade']='21'
        response = self.client.post(self.url,data)
        self.assertFalse(response.context['review'])
        self.assertContains(response, 'value="21"')
        self.assertFalse(AuditEvent.objects.exists())
        self.rows[0].refresh_from_db();self.assertIsNone(self.rows[0].grade)

    def test_certificate_requires_real_delivery_date_and_concluded_status(self):
        data = self.payload();data['participants-0-certified']='true'
        response = self.client.post(self.url,data)
        self.assertIn('certificate_delivery_date',response.context['formset'].forms[0].errors)
        data['participants-0-certificate_delivery_date']='2026-01-04'
        response = self.confirm(data);self.assertEqual(response.status_code,302)
        self.rows[0].refresh_from_db();self.assertTrue(self.rows[0].certified)
        self.assertEqual(self.rows[0].certificate_delivery_date,date(2026,1,4))
        profile = self.client.get(reverse('employee_data', args=[self.rows[0].employee_id]))
        printed = self.client.get(reverse('employee_print', args=[self.rows[0].employee_id]))
        self.assertEqual(profile.context['stats']['certificates'], 1)
        self.assertEqual(profile.context['stats'], printed.context['stats'])

    def test_outside_id_duplicate_and_missing_rows_are_rejected(self):
        for case in ['outside','duplicate','missing']:
            data=self.payload()
            if case=='outside':data['participants-1-id']=str(self.outside.pk)
            if case=='duplicate':data['participants-1-id']=str(self.rows[0].pk)
            if case=='missing':data['participants-TOTAL_FORMS']='1';data['participants-INITIAL_FORMS']='1'
            response=self.client.post(self.url,data)
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.context['formset'].is_valid())
        self.assertFalse(AuditEvent.objects.exists())

    def test_stale_review_and_modified_confirmation_are_blocked(self):
        data=self.payload();review=self.client.post(self.url,data)
        token=review.context['review_token']
        tampered={**data,'action':'confirm','review_token':token,'participants-0-grade':'18'}
        response=self.client.post(self.url,tampered)
        self.assertContains(response,'Reveja novamente')
        Training.objects.filter(pk=self.rows[1].pk).update(version=2,grade=Decimal('19'))
        response=self.client.post(self.url,{**data,'action':'confirm','review_token':token})
        self.assertContains(response,'alterações mais recentes')
        self.rows[0].refresh_from_db();self.assertIsNone(self.rows[0].grade)
        self.assertFalse(AuditEvent.objects.exists())

    def test_confirmation_requires_review_token(self):
        response=self.client.post(self.url,{**self.payload(),'action':'confirm'})
        self.assertContains(response,'Reveja novamente')
        self.assertFalse(AuditEvent.objects.exists())

    def test_atomic_rollback_if_conflict_occurs_during_save(self):
        data=self.payload();review=self.client.post(self.url,data)
        def race(request, action, detail):
            AuditEvent.objects.create(actor='batch-admin',action=action,detail=detail)
            Training.objects.filter(pk=self.rows[1].pk).update(version=2)
        with patch('training.batch.record',side_effect=race):
            response=self.client.post(self.url,{**data,'action':'confirm','review_token':review.context['review_token']})
        self.assertContains(response,'Nenhuma alteração deste lote')
        self.rows[0].refresh_from_db();self.assertIsNone(self.rows[0].grade);self.assertEqual(self.rows[0].version,1)
        self.rows[1].refresh_from_db();self.assertEqual(self.rows[1].version,1)
        self.assertFalse(AuditEvent.objects.exists())

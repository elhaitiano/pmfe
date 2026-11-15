from io import BytesIO
from datetime import date
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from .models import GroupSession, SessionAttendance, AttendanceImport, AuditEvent
from .test_sessions import group_fixture, training_fixture
from . import session_import as service

class SessionImportTests(TestCase):
    def setUp(self):
        self.group=group_fixture()
        self.row=training_fixture(self.group)
        self.session=GroupSession.objects.create(group=self.group,date=date(2025,1,30),duration=6)
        self.user=get_user_model().objects.create_user('attendance-editor',is_staff=True)
        self.client.force_login(self.user)
        self.url=reverse('group_attendance',args=[self.group.pk])
    def payload(self,mark='P',hours=None):
        book=load_workbook(BytesIO(service.template(self.group)))
        sheet=book['Presenças'];sheet['F2']=mark;sheet['G2']=hours
        stream=BytesIO();book.save(stream);return stream.getvalue()
    def review(self,content):
        self.group.refresh_from_db()
        rows=service.parse(content,self.group)
        return rows,service.plan(self.group,rows)
    def apply(self,rows,plan):
        return service.apply(self.group.pk,rows,plan['snapshot'],'attendance-editor','renamed.xlsx','Reviewed source')
    def test_preview_confirm_without_attachments_and_no_academic_inference(self):
        response=self.client.post(self.url,{'workbook':SimpleUploadedFile('daily.xlsx',self.payload()),'action':'preview'})
        self.assertEqual(response.status_code,200)
        self.assertFalse(SessionAttendance.objects.exists())
        token=response.context['confirm_form'].initial['token']
        response=self.client.post(self.url,{'action':'confirm','token':token,'confirm':'on','reason':'Signed attendance register'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(SessionAttendance.objects.get().hours,6)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.grade)
        self.assertEqual(self.row.result,'Pendente')
        self.assertIsNone(self.row.certified)
        self.assertEqual(AuditEvent.objects.filter(action='SESSION_IMPORT').count(),1)
    def test_identical_renamed_file_and_old_file_never_undo_correction(self):
        original=self.payload()
        rows,plan=self.review(original);self.apply(rows,plan)
        rows,plan=self.review(self.payload('P',2));self.assertEqual(plan['corrections'],1);self.apply(rows,plan)
        rows,plan=self.review(original)
        self.assertTrue(plan['already']);self.apply(rows,plan)
        self.assertEqual(SessionAttendance.objects.get().hours,2)
        self.assertEqual(AttendanceImport.objects.count(),2)
    def test_logical_repeat_with_decimal_representation(self):
        rows,plan=self.review(self.payload('P',6));self.apply(rows,plan)
        rows,plan=self.review(self.payload('P','6.00'));self.apply(rows,plan)
        self.assertEqual(AttendanceImport.objects.count(),1)
    def test_stale_preview_rejected_atomically(self):
        rows,plan=self.review(self.payload())
        newer,newplan=self.review(self.payload('A'));self.apply(newer,newplan)
        with self.assertRaises(ValidationError):self.apply(rows,plan)
        self.assertEqual(SessionAttendance.objects.get().hours,0)
        self.assertEqual(AttendanceImport.objects.count(),1)
    def test_formulas_unknown_people_wrong_group_and_invalid_hours_rejected(self):
        for column,value in [('F','=1+1'),('B','unknown'),('A',9999),('G',7),('E',8)]:
            book=load_workbook(BytesIO(self.payload()))
            book['Presenças'][column+'2']=value
            stream=BytesIO();book.save(stream)
            with self.subTest(column=column),self.assertRaises(ValidationError):
                self.review(stream.getvalue())
    def test_permissions_user_bound_token_and_missing_confirmation(self):
        response=self.client.post(self.url,{'workbook':SimpleUploadedFile('daily.xlsx',self.payload())})
        token=response.context['confirm_form'].initial['token']
        other=get_user_model().objects.create_user('other-editor',is_staff=True)
        self.client.force_login(other)
        self.client.post(self.url,{'action':'confirm','token':token,'confirm':'on','reason':'test'})
        self.assertFalse(SessionAttendance.objects.exists())
        other.is_staff=False;other.save()
        self.assertEqual(self.client.get(self.url).status_code,403)
    def test_unknown_and_confirmed_absence_remain_distinct(self):
        rows,plan=self.review(self.payload('?'));self.apply(rows,plan)
        record=SessionAttendance.objects.get()
        self.assertIsNone(record.hours);self.assertFalse(record.confirmed)
        rows,plan=self.review(self.payload('A'));self.apply(rows,plan)
        record.refresh_from_db()
        self.assertEqual(record.hours,0);self.assertTrue(record.confirmed)

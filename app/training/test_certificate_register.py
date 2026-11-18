import copy
import hashlib
import tempfile
import io
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Employee,Training,AuditEvent,TrainingDocumentState
from .analytics import statistics
from .certificate_register import apply_manifest,source_bytes

class CertificateRegisterTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='1234',name='Maria Manuel')
        self.t=Training.objects.create(employee=self.person,title='Formação técnica',area='Técnica',start=date(2026,1,5),end=date(2026,1,5),days=1,hours_per_day=7,attendance=1,attendance_confirmed=True,grade=0,result='Reprovado',certified=False)
        self.facts={'course_period':['2026-01-05','2026-01-06'],'total_hours':'12','certificate_number':None,'issue_date':'2026-01-07','completion_literal':True,'course_title_literal':self.t.title}
        self.manifest={'counts':[{'origin':'origem validada'}],'safe_certificates':[{'training_id':self.t.pk,'number':'1234','name':self.person.name,'facts':self.facts,'versions':[{'sha256':'a'*64,'paths':['origem.pdf'],'facts':self.facts,'method':'Identidade e edição comprovadas'}]}],'unique_pdf_contents':1,'final_pdf_versions':1,'exceptions':[],'excluded':[],'read_errors':[]}

    @patch('training.certificate_register.source_bytes',return_value=b'%PDF')
    def test_confirmation_preserves_zero_grade_attendance_and_unknown_delivery_date(self,_):
        report=apply_manifest(self.manifest);self.t.refresh_from_db()
        self.assertTrue(self.t.certified);self.assertIsNone(self.t.certificate_delivery_date)
        self.assertEqual(self.t.grade,Decimal(0));self.assertEqual(self.t.result,'Reprovado')
        self.assertEqual(self.t.attendance,1);self.assertEqual(self.t.completed_hours,Decimal(7))
        self.assertEqual(self.t.certified_hours,Decimal(12));self.assertEqual(self.t.end,date(2026,1,6))
        self.assertIsNone(TrainingDocumentState.objects.get(training=self.t).hr_delivered)
        n=AuditEvent.objects.count();self.assertEqual(apply_manifest(self.manifest),report);self.assertEqual(AuditEvent.objects.count(),n)
        stats=statistics([self.t]);self.assertEqual((stats['certificates_reported'],stats['certificate_holders'],stats['certified_hours'],stats['hours']),(1,1,Decimal(12),Decimal(7)))

    @patch('training.certificate_register.source_bytes',side_effect=ValueError('Fonte mudou'))
    def test_changed_source_prevents_all_writes(self,_):
        with self.assertRaises(ValueError):apply_manifest(self.manifest)
        self.t.refresh_from_db();self.assertFalse(self.t.certified);self.assertEqual(AuditEvent.objects.count(),0)

    def test_distinct_certificates_and_legacy_deliveries_share_holder_count(self):
        self.t.certified=True;self.t.source_data={'_certificate_summary':{'delivered_count':2,'hours':'12','hours_records':2}}
        legacy=copy.copy(self.t);legacy.source_data={};legacy.pk=900
        stats=statistics([self.t,legacy]);self.assertEqual(stats['certificates_reported'],3)
        self.assertEqual(stats['certificate_holders'],1);self.assertEqual(stats['certified_hours'],Decimal(12));self.assertEqual(stats['hours'],Decimal(14))

    def test_source_integrity_and_equivalent_locations(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'a.pdf';p.write_bytes(b'original')
            self.assertEqual(source_bytes({'sha256':hashlib.sha256(b'original').hexdigest(),'paths':[str(p)+'missing',str(p)]}),b'original')
            with self.assertRaises(ValueError):source_bytes({'sha256':'a'*64,'paths':[str(p)]})

    def test_pdf_route_requires_authenticated_authorized_user_and_saved_binding(self):
        path=reverse('certificate_source_pdf',args=[self.t.pk,'a'*64])
        self.assertEqual(self.client.get(path).status_code,302)
        user=get_user_model().objects.create_user(username='review',is_staff=True,is_superuser=True)
        self.client.force_login(user);self.assertEqual(self.client.get(path).status_code,404)

    @patch('training.certificate_register.source_bytes',return_value=b'%PDF')
    def test_evidence_import_does_not_become_a_manual_edition_on_next_write(self,_):
        from .models import ConsolidationRun,ConsolidatedEdition,ConsolidatedParticipation,HistoricalInterfaceLink
        from .interface_sync import inputs,reconcile
        ConsolidationRun.objects.create(key='test',active=True,master_sha256='a'*64)
        edition=ConsolidatedEdition.objects.create(source_key='edition',course_reference='TECH',title=self.t.title,start=self.t.start,end=self.t.end)
        p=ConsolidatedParticipation.objects.create(source_key='person',employee=self.person,edition=edition,present_count=1,absent_count=0,attendance_complete=True,attended_hours=7,grades=[{'stage':'final','value':'0','scale':'20'}])
        HistoricalInterfaceLink.objects.create(participation=p,training=self.t)
        self.t.source_data={'_EPAL_CONSOLIDATED_FACTS':{'inputs':inputs(self.t),'sync_version':self.t.version,'attended_hours':'7','edition':edition.source_key}}
        self.t.save(update_fields=['source_data'])
        apply_manifest(self.manifest);reconcile('test')
        p.refresh_from_db();self.assertEqual(p.edition_id,edition.pk)
        self.assertEqual(ConsolidatedEdition.objects.count(),1);self.assertEqual(p.attended_hours,Decimal(7))
        self.assertEqual(p.grades,[{'stage':'final','value':'0','scale':'20'}])

    @patch('training.certificate_register.source_bytes',return_value=b'%PDF')
    def test_incremental_crossing_keeps_previous_confirmations_and_audit(self,_):
        first=apply_manifest(self.manifest)
        other=Training.objects.create(employee=self.person,title='Outra formação',area='Técnica',start=date(2026,1,12),end=date(2026,1,12),days=1,hours_per_day=7,certified=None)
        manifest=copy.deepcopy(self.manifest);item=copy.deepcopy(manifest['safe_certificates'][0]);item['training_id']=other.pk
        item['facts']['course_period']=['2026-01-12','2026-01-12'];item['facts']['issue_date']='2026-01-12'
        item['versions'][0]['sha256']='b'*64;item['versions'][0]['facts']=item['facts'];manifest['safe_certificates'].append(item)
        report=apply_manifest(manifest);self.assertEqual(report['counts']['confirmed_deliveries'],2)
        self.assertEqual(report['counts']['certificates'],2);self.assertEqual(len(report['changes']),2)
        self.assertEqual(AuditEvent.objects.filter(action='CERTIFICATE_FOLDER_REGULARIZATION').count(),2)
        self.assertEqual(apply_manifest(manifest),report);self.assertEqual(first['counts']['certificates'],1)

    def test_previously_archived_certificate_hours_are_reused_without_new_delivery(self):
        from reportlab.pdfgen import canvas
        from .models import ConsolidatedEdition,ConsolidatedParticipation,HistoricalInterfaceLink,DocumentaryRecord,HistoricalCertificatePDF,HistoricalReviewSnapshot
        from .certificate_register import reuse_archived_hours
        self.t.certified=True;self.t.certificate_issued=True;self.t.save()
        e=ConsolidatedEdition.objects.create(source_key='archive-edition',course_reference='TECH',title=self.t.title,start=self.t.start,end=self.t.end)
        p=ConsolidatedParticipation.objects.create(source_key='archive-person',employee=self.person,edition=e)
        HistoricalInterfaceLink.objects.create(training=self.t,participation=p)
        record=DocumentaryRecord.objects.create(source_key='archive-proof',kind='certificate_reference',employee=self.person,state='NATIVE',payload={},payload_sha256='a'*64,package_sha256='b'*64)
        output=io.BytesIO();c=canvas.Canvas(output);c.drawString(20,800,'CERTIFICADO');c.drawString(20,780,'Maria Manuel')
        c.drawString(20,760,'Concluiu a accao de formacao: Formacao tecnica, realizada no dia 5 de Janeiro de 2026')
        c.drawString(20,740,'com duracao de 7 horas.');c.save();raw=output.getvalue();sha=hashlib.sha256(raw).hexdigest()
        pdf=HistoricalCertificatePDF.objects.create(training=self.t,source_record=record,sha256=sha,content=raw,source_reference='certificado-arquivado.pdf',binding_evidence={'employee_number':'1234','review_state':'PERIODO_CONCORDANTE'})
        HistoricalReviewSnapshot.objects.create(key='archive-start',actor='test',report={'certificate_folder_regularization':{'changes':[],'counts':{}}})
        changes=reuse_archived_hours();self.assertEqual(len(changes),1)
        self.t.refresh_from_db();self.assertEqual(self.t.certified_hours,Decimal(7));self.assertTrue(self.t.certified);self.assertIsNone(self.t.certificate_delivery_date)
        self.assertEqual(source_bytes({'archive_id':pdf.pk,'sha256':sha,'paths':[]}),raw)
        self.assertEqual(reuse_archived_hours(),[])

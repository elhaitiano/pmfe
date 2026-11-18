from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.template.loader import render_to_string
from django import forms
from .models import Employee,Training
from .analytics import statistics,document_value
from .documents import delivery_summary
from .widgets import date_widgets

class RecordPresentationTests(TestCase):
    def setUp(self):
        self.e=Employee.objects.create(number='1234',name='Maria Manuel')

    def row(self,**kw):
        args=dict(employee=self.e,title='Teste',area='Técnica',start=date(2026,1,5),end=date(2026,1,5),days=1,hours_per_day=7)
        args.update(kw);return Training.objects.create(**args)

    def test_only_issued_certificates_without_delivery_are_pending(self):
        unknown=self.row(certificate_issued=None,certified=None)
        not_issued=self.row(certificate_issued=False,certified=None)
        pending=self.row(certificate_issued=True,certified=None)
        delivered=self.row(certificate_issued=True,certified=True)
        stats=statistics([unknown,not_issued,pending,delivered])
        self.assertEqual(stats['certificates_reported_pending'],1)
        self.assertEqual(stats['certificate_emission_unrecorded'],1)
        self.assertEqual(stats['certificates_reported'],1)

    def test_document_profile_uses_confirmed_main_record_without_supplement(self):
        t=self.row(certificate_issued=True,certified=True)
        self.assertTrue(document_value(t,'issued'));self.assertTrue(document_value(t,'learner_delivered'))
        summary=delivery_summary(t);self.assertEqual(summary['issued'],'Sim');self.assertEqual(summary['learner'],'Sim')
        self.assertIsNone(document_value(t,'hr_delivered'))

    def test_known_certificate_hours_do_not_fabricate_presence(self):
        t=self.row(certificate_issued=True,certified=True,source_data={'_certificate_summary':{'hours':'28','hours_records':1,'delivered_count':1}})
        html=render_to_string('hours_cell.html',{'t':t})
        self.assertIn('28 h certificadas',html);self.assertIn('Horas de presença sem registo',html)
        self.assertNotIn('Por confirmar',html);self.assertIsNone(t.completed_hours)

    def test_choice_caption_keeps_persisted_value(self):
        field=forms.ChoiceField(choices=[('Por confirmar','Por confirmar'),('Concluído','Concluído')])
        date_widgets({'status':field});self.assertEqual(list(field.choices)[0],('Por confirmar','Conclusão não documentada'))

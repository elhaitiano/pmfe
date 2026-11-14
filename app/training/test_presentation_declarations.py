from datetime import date
from decimal import Decimal
from django.test import SimpleTestCase
from .models import Training

class PresentationDeclarationsTests(SimpleTestCase):
 def row(self):
  return Training(start=date(2025,1,1),end=date(2025,1,2),status='Concluído',source_data={'_presentation_grade':{'grade':'17.25','simulated':True},'_certificate_delivery_declaration':{'declared':True,'delivery_date':'2025-01-02'}})
 def test_simulation_does_not_replace_original_grade(self):
  t=self.row();self.assertIsNone(t.grade);self.assertEqual(t.presentation_grade,Decimal('17.25'));self.assertEqual(t.result,'Pendente')
 def test_declaration_not_documentary_confirmation(self):
  t=self.row();self.assertEqual(t.declared_delivery_date,t.end);self.assertIsNone(t.certified);self.assertIsNone(t.certificate_delivery_date);self.assertFalse(t.delivery_receipt_associated)
 def test_planned_cannot_have_declared_delivery(self):
  t=self.row();t.status='Planeado';self.assertIsNone(t.declared_delivery_date)
 def test_declaration_must_equal_end(self):
  t=self.row();t.source_data['_certificate_delivery_declaration']['delivery_date']='2025-01-01';self.assertIsNone(t.declared_delivery_date)

 def test_reported_delivery_label_preserves_documentary_fields(self):
  t=self.row();self.assertEqual(t.certificate_label,'Entregue');self.assertIsNone(t.certified);self.assertIsNone(t.certificate_issued)
 def test_shared_delivery_cell_shows_declaration_date(self):
  from django.template.loader import render_to_string
  html=render_to_string('certificate_delivery_cell.html',{'participation':self.row()})
  self.assertIn('Entregue',html);self.assertIn('02/01/2025',html);self.assertNotIn('Por confirmar',html)
 def test_shared_grade_cell_uses_integer_presentation_without_overwriting(self):
  from django.template.loader import render_to_string
  t=self.row();html=render_to_string('original_note_cell.html',{'t':t})
  self.assertIn('17',html);self.assertNotIn('/20',html);self.assertNotIn('Sem nota',html);self.assertIsNone(t.grade)

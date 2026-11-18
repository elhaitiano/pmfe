from types import SimpleNamespace
from django.test import SimpleTestCase
from .historical_regularization import has_conflicting_claims
from .certificate_evidence import certificate_facts,course_period


class EvidenceReviewTests(SimpleTestCase):
    def record(self,statuses):
        return SimpleNamespace(state='PRESENTE_DECLARADO',payload={'claims':[
            {'status':status,'eligible_documentary_assertion':True,'source':{}} for status in statuses]})

    def test_concordance_flag_does_not_hide_different_eligible_marks(self):
        record=self.record(['PRESENTE_DECLARADO','AUSENTE_DECLARADO'])
        record.payload['documentary_consistent']=True
        self.assertTrue(has_conflicting_claims(record))

    def test_unsigned_copy_does_not_create_conflict_with_valid_source(self):
        record=self.record(['PRESENTE_DECLARADO','AUSENTE_DECLARADO'])
        record.payload['claims'][1]['source']['preparatory_or_derived_or_synthetic_source']=True
        self.assertFalse(has_conflicting_claims(record))

    def test_repeated_same_mark_is_one_concordant_value(self):
        self.assertFalse(has_conflicting_claims(self.record(['PRESENTE_DECLARADO']*4)))

    def test_issue_date_is_not_the_course_period(self):
        self.assertIsNone(course_period('Luanda, 13 de Fevereiro de 2026'))
        self.assertEqual(course_period('Realizada nos dias 02 a 05 de Fevereiro de 2026. Luanda, 13 de Fevereiro de 2026'),['2026-02-02','2026-02-05'])

    def test_name_is_not_a_certificate_number(self):
        self.assertIsNone(certificate_facts(['CERTIFICADO\nNuno Manuel\nCertificamos que:'])['certificate_number'])

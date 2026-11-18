from django.test import SimpleTestCase
from .certificate_evidence import recipient_names

class CertificateNameTests(SimpleTestCase):
    def test_surname_on_separate_line_is_retained(self):
        self.assertEqual(recipient_names('C E R T I F I C A D O\nAdelina I. dos S. M.\nCacombo\nLuanda, 13 de Fevereiro de 2026\nCertificamos que :'),['Adelina I. dos S. M. Cacombo'])

    def test_signature_is_not_part_of_name(self):
        self.assertEqual(recipient_names('CERTIFICADO\nMaria Manuel\nCertificamos que :\nALAIN CARBONEL\nDIRECTOR PROAGUA'),['Maria Manuel'])

    def test_no_caption_does_not_use_filename_or_assume_a_name(self):
        self.assertEqual(recipient_names('Termos de referência\nMaria Manuel\nFormação'),[])

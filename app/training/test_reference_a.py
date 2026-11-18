from django.test import SimpleTestCase,TestCase,RequestFactory
from django.template.loader import render_to_string
from django.template import Context
from .templatetags.reference_a import reference_a_identity
from .models import ConsolidationRun,AuditEvent

class ReferenceAPresentationTests(SimpleTestCase):
    def test_reference_preview_without_request_does_not_need_database(self):
        identity=reference_a_identity(Context({}))
        self.assertFalse(identity['official'])
        self.assertIn('Base provisória',identity['caption'])

    def test_explicit_official_context_never_recovers_provisional_claim(self):
        identity=reference_a_identity(Context({'consolidated_mode':True}))
        self.assertTrue(identity['official']);self.assertIn('Cadastro oficial EPAL',identity['caption'])
        self.assertNotIn('provisória',identity['caption'])

    def test_historical_group_not_mislabelled_as_cancelled(self):
        from types import SimpleNamespace
        from datetime import date
        group=SimpleNamespace(pk=1,code='G1',title='Formação histórica',start=date(2026,1,1),end=date(2026,1,2))
        html=render_to_string('planning/group.html',{'group':group,'historical_edition':True,'attendance_open':False,
            'roster':{'historical_only':True,'members':[],'display_state':'Histórico'},'participant_count':0})
        self.assertIn('Registo histórico: consulte as listas históricas em PDF ou Excel',html)
        self.assertNotIn('Grupo cancelado: lista indisponível',html)
        self.assertIn('período registado no histórico',html)
        group.state='Cancelada'
        html=render_to_string('planning/group.html',{'group':group,'attendance_open':False,'roster':{'members':[]}})
        self.assertIn('Grupo cancelado: lista indisponível',html)

    def test_new_validation_keeps_original_executive_opening_and_actual_values(self):
        html=render_to_string('dashboard.html',{'consolidated_mode':True,'has_rows':True,'insights':['Critério existente'],
            'main_kpis':[{'label':'Nota actual','value':0}], 'stats':{'certificates_reported':760,'certificate_holders':503},
            'group_validation_metrics':{'validated':1,'review':0,'completed':1,'approved':0,'rejected':0,'undefined':1,'academic_denominator':0}})
        self.assertLess(html.index('id="viewDashboard"'),html.index('id="group-validation-title"'))
        self.assertLess(html.index('exec-primary-kpis'),html.index('id="group-validation-title"'))
        self.assertIn('760',html);self.assertIn('503',html)
        self.assertIn('Validação de grupos',html);self.assertIn('aprovação académica',html)
        self.assertNotIn('Base provisória',html)

class ReferenceAIdentityQueriesTests(TestCase):
    def test_official_caption_cached_within_request_and_no_audit_or_business_write(self):
        ConsolidationRun.objects.create(key='synthetic-reference-A',active=True,master_sha256='a'*64,report={})
        request=RequestFactory().get('/dashboard/')
        with self.assertNumQueries(1):
            a=reference_a_identity(Context({'request':request}))
            b=reference_a_identity(Context({'request':request}))
        self.assertTrue(a['official']);self.assertEqual(a,b)
        self.assertEqual(AuditEvent.objects.count(),0)

    def test_request_cache_does_not_leak_between_official_and_demo_databases(self):
        first=reference_a_identity(Context({'request':RequestFactory().get('/')}))
        self.assertFalse(first['official'])
        ConsolidationRun.objects.create(key='synthetic-reference-A',active=True,master_sha256='a'*64,report={})
        second=reference_a_identity(Context({'request':RequestFactory().get('/')}))
        self.assertTrue(second['official'])

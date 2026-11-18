from decimal import Decimal
from django.test import SimpleTestCase,TestCase
from .source_arithmetic import evaluate
from .course_identity import title_signature
from .consolidation import stats,parse_official_date,workers
from .models import Employee,OfficialRosterEntry,ConsolidationRun,ConsolidatedEdition,ConsolidatedParticipation


class SourceRulesTests(SimpleTestCase):
    def test_source_conversion_does_not_change_original_scale(self):
        self.assertEqual(evaluate('=H9/5',{'H9':87}),Decimal('17.4'))
        self.assertEqual(evaluate('=IF(K9<=11," ",IF(K9<=14,"Bom","Muito Bom"))',{'K9':Decimal('12.66')}),'Bom')

    def test_missing_or_executable_formula_never_becomes_zero(self):
        for formula in ['=H9/5','=H9/0','=__import__("os").system("echo x")','=open("private")']:
            self.assertIsNone(evaluate(formula,{}))
        self.assertIsNone(evaluate('=H9/0',{'H9':80}))

    def test_actual_zero_is_preserved(self):
        self.assertEqual(evaluate('=H9/5',{'H9':0}),Decimal(0))

    def test_course_levels_are_distinct(self):
        self.assertNotEqual(title_signature('Excel básico'),title_signature('Excel avançado'))
        self.assertNotEqual(title_signature('Bombagem I'),title_signature('Bombagem II'))

    def test_no_guessed_date(self):
        self.assertIsNone(parse_official_date('31-02-2026'))
        self.assertEqual(str(parse_official_date('30-10-2026')),'2026-10-30')


class ConsolidationCountsTests(TestCase):
    def test_roster_and_denominators_are_independent_from_documents(self):
        person=Employee.objects.create(number='001',name='Pessoa EPAL')
        Employee.objects.create(number='002',name='Histórico preservado')
        OfficialRosterEntry.objects.create(employee=person,current=True,source_sha256='a'*64,source_row=12)
        ConsolidationRun.objects.create(key='test',active=True,master_sha256='a'*64,report={})
        edition=ConsolidatedEdition.objects.create(source_key='edition',course_reference='CURSO',title='Curso',group_label='1')
        p=ConsolidatedParticipation.objects.create(source_key='part',employee=person,edition=edition,grades=[
            {'stage':'final','value':'80','scale':'100'}, {'stage':'certificate_mention','value':'16','scale':'20'}])
        result=stats([p])
        self.assertEqual(result['people'],1);self.assertEqual(result['participations'],1)
        self.assertEqual(result['with_grades'],1);self.assertEqual(result['with_attendance'],0)
        self.assertIsNone(result['attendance']);self.assertEqual(len(result['grade_averages']),2)
        self.assertEqual(workers().count(),1);self.assertEqual(Employee.objects.count(),2)

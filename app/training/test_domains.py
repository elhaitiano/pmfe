import importlib
from datetime import date
from django.test import SimpleTestCase, TestCase
from .domains import DOMAINS, GESTAO, SEGURANCA, TECNICO, TI, domain_choices, normalize_area
from .forms import TrainingForm
from .models import Course, Employee, Training, TrainingGroup
from .planning import CourseForm

migration = importlib.import_module('training.migrations.0029_training_domains')


class LegacyAreaTests(SimpleTestCase):
    def test_old_areas_map_to_the_four_domains(self):
        expected = {'Técnica': TECNICO, 'Segurança': SEGURANCA, 'TI / Sistemas': TI, 'Soft skills': GESTAO,
                    'Comunicação': GESTAO, 'Liderança': GESTAO, 'Emocional': GESTAO, 'Cliente': GESTAO,
                    'Social': GESTAO, ' social ': GESTAO}
        for old, domain in expected.items():
            with self.subTest(old=old):
                self.assertEqual(normalize_area(old), domain)

    def test_unknown_text_is_kept_not_erased(self):
        self.assertEqual(normalize_area('Ambiente'), 'Ambiente')
        self.assertEqual(normalize_area(''), '')

    def test_choices_are_the_four_domains_and_keep_an_unclassified_value(self):
        self.assertEqual([v for v, _ in domain_choices()], [''] + list(DOMAINS))
        self.assertIn(('Ambiente', 'Ambiente (por classificar)'), domain_choices('Ambiente'))


class TitleRuleTests(SimpleTestCase):
    def test_titles_follow_the_domain_definitions(self):
        cases = {
            'Manuseamento seguro de cloro gasoso': SEGURANCA,
            'Combate a incêndios': SEGURANCA,
            'Primeiros socorros': SEGURANCA,
            'Segurança na manutenção de bombas': SEGURANCA,
            'Excel avançado': TI,
            'Gestão de manutenção assistida por computador (GMAO)': TI,
            'Tratamento de água': TECNICO,
            'Leitura de contadores': TECNICO,
            'Manutenção de equipamentos electromecânicos': TECNICO,
            'Análises laboratoriais': TECNICO,
            'Secretariado geral': GESTAO,
            'Comunicação assertiva': GESTAO,
            'Atendimento ao público': GESTAO,
            'Gestão de equipas': GESTAO,
        }
        for title, domain in cases.items():
            with self.subTest(title=title):
                self.assertEqual(migration.by_title(title)[0], domain)

    def test_title_without_rule_stays_unconfirmed(self):
        self.assertEqual(migration.by_title('Curso X')[0], '')

    def test_course_domain_wins_over_the_title(self):
        self.assertEqual(migration.classify('', 'Excel', 'Técnica'), (TECNICO, 'domínio da acção do catálogo'))
        self.assertEqual(migration.classify('Social', 'Excel'), (GESTAO, 'área antiga'))


class SaveAndFormTests(TestCase):
    def test_new_records_are_normalised_on_save(self):
        course = Course.objects.create(code='DOM1', title='Curso', area='TI / Sistemas')
        person = Employee.objects.create(number='D1', name='Synthetic')
        row = Training.objects.create(employee=person, title='Curso', area='Liderança', start=date(2026, 1, 1),
                                      end=date(2026, 1, 1), attendance=1, status='Concluído')
        self.assertEqual(Course.objects.get(pk=course.pk).area, TI)
        self.assertEqual(Training.objects.get(pk=row.pk).area, GESTAO)

    def test_forms_offer_the_four_domains(self):
        for form in (TrainingForm(), CourseForm()):
            with self.subTest(form=type(form).__name__):
                values = [v for v, _ in form.fields['area'].choices]
                self.assertEqual(values, [''] + list(DOMAINS))
                self.assertEqual(form.fields['area'].label, 'Domínio de competências')

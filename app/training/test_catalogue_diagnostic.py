"""Run with unittest and a dedicated SQLite :memory: Django configuration."""
import copy
import json
import unittest
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from .catalogue_diagnostic import EXCLUDED_SOURCE_KEYS, analyse_catalogue
from .management.commands.diagnose_catalogue import select_only
from .models import Course, Employee, Enrollment, GroupSession, Training, TrainingGroup


def row(title='Segurança', **changes):
    return dict(dict(title=title, area='Técnica', days=2,
                     hours_per_day=Decimal('7'), source_key=None), **changes)


class AnalysisTests(unittest.TestCase):
    def test_normalisation_preserves_variants_and_inputs(self):
        rows = [row(' SEGURANÇA  '), row('Segurança'), row('ＳＥＧＵＲＡＮÇＡ')]
        before = copy.deepcopy(rows)
        report = analyse_catalogue(iter(rows))
        self.assertEqual(report['summary']['candidate_actions'], 1)
        self.assertEqual(report['summary']['analysed_participations'], 3)
        self.assertEqual(len(report['candidates'][0]['title_variants']), 3)
        self.assertEqual(rows, before)

    def test_all_documented_duplicates_excluded_without_mutation(self):
        rows = [row('Original')]
        rows += [row('Duplicado', source_key=k) for k in EXCLUDED_SOURCE_KEYS]
        report = analyse_catalogue(rows)
        self.assertEqual(report['summary']['excluded_duplicates'], 5)
        self.assertEqual(report['summary']['analysed_participations'], 1)
        self.assertEqual(len(rows), 6)

    def test_existing_alias_collision_and_divergent_metadata(self):
        courses = [{'id': 1, 'title': 'Acção A', 'aliases': ['Segurança']},
                   {'id': 2, 'title': 'Segurança', 'aliases': []}]
        rows = [row(), dict(row(), area='', days=3)]
        report = analyse_catalogue(rows, courses)
        candidate = report['candidates'][0]
        self.assertEqual(candidate['existing_course_ids'], [1, 2])
        self.assertEqual(len(candidate['validation_reasons']), 3)
        self.assertTrue(candidate['needs_validation'])

    def test_possible_alias_never_automatically_merged(self):
        report = analyse_catalogue([row('Segurança'), row('Seguranca')])
        self.assertEqual(report['summary']['candidate_actions'], 2)
        self.assertEqual(report['summary']['possible_alias_pairs'], 1)
        self.assertTrue(all(c['needs_validation'] for c in report['candidates']))

    def test_empty_history_and_empty_title(self):
        self.assertEqual(analyse_catalogue([])['summary']['candidate_actions'], 0)
        self.assertEqual(analyse_catalogue([row('  ')])['summary']['empty_title_participations'], 1)

    def test_sql_guard_rejects_mutations_before_execution(self):
        execute = Mock()
        for sql in ['INSERT INTO x VALUES (1)', 'UPDATE x SET a=1',
                    'DELETE FROM x', 'CREATE TABLE x(a)', 'PRAGMA user_version=1',
                    'WITH x AS (SELECT 1) DELETE FROM y']:
            with self.subTest(sql=sql), self.assertRaises(CommandError):
                select_only(execute, sql, (), False, {})
        execute.assert_not_called()


class InMemoryCommandTests(unittest.TestCase):
    models = (Employee, Course, TrainingGroup, Training, GroupSession, Enrollment)

    @classmethod
    def setUpClass(cls):
        # Refuse even fixture creation unless this is explicitly disposable memory.
        if (connection.vendor != 'sqlite' or
                connection.settings_dict['NAME'] != ':memory:'):
            raise unittest.SkipTest('Requires dedicated SQLite :memory: configuration.')
        with connection.schema_editor() as editor:
            for model in cls.models:
                editor.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            for model in reversed(cls.models):
                editor.delete_model(model)

    def test_command_reads_only_metadata_and_changes_no_rows(self):
        employee = Employee.objects.create(number='SYNTHETIC', name='Synthetic fixture')
        for key in ['pilar3b:PF-01852', *EXCLUDED_SOURCE_KEYS]:
            Training.objects.create(employee=employee, title='Segurança', area='Técnica',
                                    start=date(2025, 1, 1), end=date(2025, 1, 2),
                                    days=2, hours_per_day=7, attendance=2, source_key=key)
        Course.objects.create(title='Segurança', aliases=['Seguranca'])
        before = {m.__name__: list(m.objects.order_by('pk').values()) for m in self.models}
        changes_before = connection.connection.total_changes
        queries = []

        def observe(execute, sql, params, many, context):
            queries.append(sql)
            return execute(sql, params, many, context)

        output = StringIO()
        with connection.execute_wrapper(observe):
            call_command('diagnose_catalogue', stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report['summary']['analysed_participations'], 1)
        self.assertEqual(report['summary']['excluded_duplicates'], 5)
        self.assertEqual(report['summary']['new_candidates'], 0)
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(q.lstrip().upper().startswith('SELECT ') for q in queries))
        self.assertNotIn('training_employee', ' '.join(queries))
        for field in ['employee_id', 'source_data', 'attendance', 'grade', 'certified']:
            self.assertNotIn('"' + field + '"', ' '.join(queries))
        self.assertEqual(connection.connection.total_changes, changes_before)
        after = {m.__name__: list(m.objects.order_by('pk').values()) for m in self.models}
        self.assertEqual(after, before)
        second = StringIO()
        call_command('diagnose_catalogue', stdout=second)
        self.assertEqual(json.loads(second.getvalue()), report)

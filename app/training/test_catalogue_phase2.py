"""Synthetic catalogue tests; Django runner must use a disposable test database."""
import copy
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from .catalogue_import import (HEADERS, SOURCE_SYSTEM, apply_plan, read_catalogue, simulate)
from .models import AuditEvent, Course, Employee, Enrollment, GroupSession, Training, TrainingGroup
from .planning import CourseForm, create_group


def source(identifier='AC-SYN-001', title='Synthetic course', code='SYN'):
    return dict(id=identifier, title=title, code=code, area='', days=None,
                hours=None, origin='Synthetic fixture', designation_state='Catálogo do plano')


def approve(rows, actor='synthetic reviewer'):
    plan = simulate(rows)
    return apply_plan(rows, plan, plan['plan_digest'], actor)


class CataloguePresentationTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('synthetic-catalogue-reader', is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse('planning')
        self.workshop = Course.objects.create(title='Workshop Ser feliz no trabalho',
            source_system='synthetic', source_action_id='AC-2026-051',
            source_internal_code='WSFR', internal_code='WSFT',
            source_metadata={'code':'WSFR','origin':'Synthetic','days':'1','hours':'7'},
            validation_notes='Synthetic documented code decision. Area pending.')
        self.blocked = Course.objects.create(title='RDSC designation pending',
            source_system='synthetic', source_action_id='HC-RDSC', source_internal_code='RDSC',
            area='Técnica', validation_status='blocked')
        self.validated = Course.objects.create(title='Manual approved action', area='Social',
            internal_code='MAN01', validation_status='validated', validation_notes='Synthetic review')
        self.archived = Course.objects.create(title='Archived pending action', active=False)

    def filler(self, count=53):
        Course.objects.bulk_create([Course(title=f'Filtro & pesquisa {i:03}',
            key=f'filtro & pesquisa {i:03}', area='Área & Especial') for i in range(count)])

    def test_search_covers_title_area_approved_original_code_and_origin_id(self):
        for query in ['Workshop', 'wsft', 'wsfr', 'AC-2026-051']:
            with self.subTest(query=query):
                response = self.client.get(self.url, {'q':query})
                self.assertEqual(list(response.context['courses']), [self.workshop])
        for query in ['MAN01', 'Social']:
            self.assertEqual(list(self.client.get(self.url, {'q':query}).context['courses']), [self.validated])

    def test_filters_combine_and_global_indicators_do_not_change(self):
        expected = {'total':4, 'pending':2, 'validated':1, 'blocked':1}
        cases = [({},4), ({'validation':'blocked'},1), ({'area':'__undefined__'},2),
                 ({'area':'Social','validation':'validated'},1),
                 ({'q':'WSFR','validation':'pending','area':'__undefined__'},1),
                 ({'area':'Social','validation':'pending'},0)]
        for params,count in cases:
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.context['course_page'].paginator.count, count)
                self.assertEqual(response.context['catalogue_stats'], expected)
        self.assertEqual(list(self.client.get(self.url).context['areas']), ['Social','Técnica'])

    def test_catalogue_pagination_24_preserves_all_records(self):
        self.filler()
        identifiers = []
        for number,expected in [(1,24),(2,24),(3,9)]:
            response = self.client.get(self.url, {'course_page':number})
            self.assertEqual(len(response.context['courses']), expected)
            identifiers.extend(c.pk for c in response.context['courses'])
        self.assertEqual(len(set(identifiers)), 57)
        self.assertEqual(set(identifiers), set(Course.objects.values_list('pk',flat=True)))

    def test_group_and_catalogue_pages_are_independent_and_keep_get_parameters(self):
        from urllib.parse import parse_qs
        self.filler()
        TrainingGroup.objects.bulk_create([TrainingGroup(course=self.validated, number=i+1,
            title=f'Synthetic group {i}', area='Social', trainer='Synthetic', location='Synthetic',
            hours_per_day=7, start_time=time(8), end_time=time(16),
            start=date(2025,1,1), end=date(2025,1,1), days=1) for i in range(25)])
        response = self.client.get(self.url, {'course_page':2,'page':2})
        self.assertEqual(response.context['course_page'].number, 2)
        self.assertEqual(response.context['page'].number, 2)
        self.assertEqual(response.context['page'].paginator.count, 25)
        response = self.client.get(self.url, {'q':'Filtro &','area':'Área & Especial',
            'validation':'pending','course_page':2,'page':2})
        params = parse_qs(response.context['catalogue_query'])
        self.assertEqual(params['q'], ['Filtro &'])
        self.assertEqual(params['area'], ['Área & Especial'])
        self.assertEqual(params['validation'], ['pending'])
        self.assertEqual(params['page'], ['2'])
        self.assertNotIn('course_page', params)
        self.assertEqual(parse_qs(response.context['group_query'])['course_page'], ['2'])
        self.assertNotIn('page', parse_qs(response.context['group_query']))

    def test_invalid_filters_empty_results_and_invalid_pages_render_safely(self):
        response = self.client.get(self.url, {'validation':'unknown','area':'not registered','course_page':'invalid'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['validation'], '')
        self.assertEqual(response.context['area'], '')
        self.assertEqual(response.context['course_page'].number, 1)
        response = self.client.get(self.url, {'q':'<script>no-match</script>'})
        self.assertContains(response, 'Nenhuma acção corresponde')
        self.assertNotContains(response, '<script>no-match</script>')
        self.assertEqual(response.context['catalogue_stats']['total'], 4)
        self.filler()
        self.assertEqual(self.client.get(self.url, {'course_page':999}).context['course_page'].number, 3)

    def test_rendering_has_accessible_details_separate_codes_and_protected_actions(self):
        self.workshop.title = 'Long synthetic title ' * 25
        self.workshop.save()
        response = self.client.get(self.url)
        for text in ['WSFT','WSFR','Código aprovado','Código de origem','Ver detalhes',
                     'Divergência de código documentada','Área por definir','Horas por confirmar',
                     'Pendente','Bloqueada','Validada','Arquivada no catálogo']:
            self.assertContains(response, text)
        self.assertContains(response, self.workshop.title)
        self.assertContains(response, '<details class="plan-course-details">')
        self.assertContains(response, 'for="catalogue-search"')
        self.assertContains(response, 'aria-label="Paginação das acções"')
        for course in [self.workshop,self.blocked,self.archived]:
            self.assertNotContains(response, reverse('group_create', args=[course.pk]))
        for course in [self.workshop,self.blocked]:
            self.assertNotContains(response, reverse('course_delete', args=[course.pk]))
        self.assertContains(response, reverse('group_create', args=[self.validated.pk]))
        self.assertContains(response, reverse('course_delete', args=[self.validated.pk]))


class CatalogueStructureTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('synthetic-admin', is_staff=True)
        self.client.force_login(self.user)
        self.values = dict(dates=[date(2025, 1, 1)], trainer='Synthetic trainer', provider='',
                           location='Synthetic room', capacity=10, hours_per_day=7,
                           break_hours=1, start_time=time(8), end_time=time(16))

    def test_defaults_are_unknown_and_active_independent(self):
        course = Course.objects.create(title='Unknown duration')
        self.assertIsNone(course.default_days)
        self.assertIsNone(course.default_hours)
        self.assertTrue(course.active)
        self.assertEqual(course.validation_status, 'pending')
        for state, active in [('pending', True), ('blocked', True), ('validated', False)]:
            course.validation_status, course.active = state, active
            course.save()
            with self.assertRaises(ValidationError):
                create_group(course, self.values)
            response = self.client.post(reverse('group_create', args=[course.pk]), self.values)
            self.assertEqual(response.status_code, 302)
        self.assertEqual(TrainingGroup.objects.count(), 0)
        self.assertEqual(GroupSession.objects.count(), 0)
        course.refresh_from_db()
        self.assertEqual(course.next_group, 1)

    def test_validated_course_allows_explicit_group_duration(self):
        course = Course.objects.create(title='Reviewed', validation_status='validated',
                                       validation_notes='Synthetic documented decision')
        self.assertEqual(create_group(course, self.values).code, 'G1')

    def test_server_rechecks_validation_after_object_was_loaded(self):
        course = Course.objects.create(title='Initially approved', validation_status='validated')
        Course.objects.filter(pk=course.pk).update(validation_status='blocked')
        with self.assertRaises(ValidationError):
            create_group(course, self.values)
        self.assertEqual(TrainingGroup.objects.count(), 0)
        course.refresh_from_db()
        self.assertEqual(course.next_group, 1)

    def test_form_and_templates_handle_unknown_values_and_all_states(self):
        for state in ['pending', 'blocked', 'validated']:
            Course.objects.create(title='State ' + state, validation_status=state,
                                  validation_notes='Synthetic review')
        archived = Course.objects.create(title='Archived', active=False)
        page = self.client.get(reverse('planning'))
        self.assertEqual(page.status_code, 200)
        for label in ['Pendente', 'Bloqueada', 'Validada', 'Arquivada no catálogo']:
            self.assertContains(page, label)
        self.assertNotContains(page, 'None')
        self.assertNotContains(page, reverse('group_create', args=[archived.pk]))
        reviewed = Course.objects.get(validation_status='validated')
        form_page = self.client.get(reverse('group_create', args=[reviewed.pk]))
        self.assertEqual(form_page.status_code, 200)
        self.assertContains(form_page, 'id="planDayCount"')
        self.assertNotContains(form_page, 'value="None"')
        form = CourseForm(dict(title='Manual pending', area='', contents='', default_days='',
                               default_hours='', internal_code='', validation_status='pending',
                               validation_notes='', active='on'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['default_hours'])

    def test_validation_requires_documentation_and_workshop_decision(self):
        c = Course(title='Workshop synthetic', key='workshop synthetic',
                   source_internal_code='WSFR', validation_status='validated')
        with self.assertRaises(ValidationError):
            c.full_clean()
        c.validation_notes = 'Synthetic documentary decision confirms WSFT'
        with self.assertRaises(ValidationError):
            c.full_clean()
        c.internal_code = 'WSFT'
        c.full_clean()

    def test_source_identity_and_approved_code_constraints(self):
        Course.objects.create(title='One', source_system='synthetic', source_action_id='ONE', internal_code='CODE')
        for values in [dict(source_system='synthetic', source_action_id='ONE'),
                       dict(internal_code='code'), dict(source_system='synthetic')]:
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                Course.objects.create(title='Other', **values)


class CatalogueImportTests(TestCase):
    def reviewed_workshop(self):
        rows = [source('AC-2026-051', 'Workshop "Ser feliz no trabalho"', 'WSFR'),
                source('HC-RDSC', 'Designation pending', 'RDSC'), source()]
        rows[1]['designation_state'] = 'Designação por confirmar'
        baseline = simulate(rows)
        decision = dict(source_action_id='AC-2026-051',
                        source_digest=baseline['operations'][0]['values']['source_digest'],
                        source_internal_code='WSFR', internal_code='WSFT',
                        approval_note='Synthetic explicit confirmation by catalogue reviewer')
        return rows, baseline, decision

    def test_reviewed_code_preserves_source_and_other_actions_without_full_validation(self):
        rows, baseline, decision = self.reviewed_workshop()
        reviewed = simulate(rows, reviewed_decisions=[decision])
        values = reviewed['operations'][0]['values']
        self.assertEqual(values['source_internal_code'], 'WSFR')
        self.assertEqual(values['source_metadata']['code'], 'WSFR')
        self.assertEqual(values['internal_code'], 'WSFT')
        self.assertEqual(values['validation_status'], 'pending')
        self.assertIn(decision['approval_note'], values['validation_notes'])
        self.assertEqual(reviewed['operations'][1:], baseline['operations'][1:])
        self.assertEqual(reviewed['operations'][1]['values']['validation_status'], 'blocked')
        self.assertNotEqual(reviewed['plan_digest'], baseline['plan_digest'])
        self.assertEqual(Course.objects.count(), 0)

    def test_reviewed_plan_is_reproducible_and_repeat_preserves_code(self):
        rows, baseline, decision = self.reviewed_workshop()
        plan = simulate(rows, reviewed_decisions=[decision])
        self.assertEqual(apply_plan(rows, plan, plan['plan_digest'], 'Synthetic reviewer')['created'], 3)
        self.assertEqual(Course.objects.get(source_action_id='AC-2026-051').internal_code, 'WSFT')
        repeated = simulate(rows, reviewed_decisions=[decision])
        self.assertEqual(apply_plan(rows, repeated, repeated['plan_digest'], 'Synthetic reviewer')['created'], 0)
        self.assertEqual(Course.objects.count(), 3)

    def test_review_refuses_changed_source_and_unapproved_fields(self):
        rows, baseline, decision = self.reviewed_workshop()
        for change in [dict(source_digest='changed'), dict(source_internal_code='WSFT'),
                       dict(validation_status='validated'), dict(approval_note='')]:
            bad = dict(decision, **change)
            with self.subTest(change=change), self.assertRaises(CommandError):
                simulate(rows, reviewed_decisions=[bad])
        with self.assertRaises(CommandError):
            simulate(rows, reviewed_decisions=[decision, decision])

    def test_review_refuses_approved_code_collisions(self):
        rows, baseline, decision = self.reviewed_workshop()
        Course.objects.create(title='Other approved action', internal_code='wsft')
        with self.assertRaises(CommandError):
            simulate(rows, reviewed_decisions=[decision])
        Course.objects.all().delete()
        rows[-1]['code'] = 'WSFT'
        with self.assertRaises(CommandError):
            simulate(rows, reviewed_decisions=[decision])

    def test_simulation_is_select_only_and_does_not_approve_anything(self):
        rows = [source(), source('AC-2026-051', 'Workshop "Ser feliz no trabalho"', 'WSFR'),
                source('AC-SYN-003', 'No code', '')]
        original = copy.deepcopy(rows)
        statements = []
        def observe(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(observe):
            plan = simulate(rows)
        self.assertTrue(all(sql.upper().startswith('SELECT ') for sql in statements))
        self.assertEqual(Course.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertEqual(rows, original)
        self.assertEqual(plan['operations'][1]['values']['validation_status'], 'blocked')
        for operation in plan['operations']:
            self.assertEqual(operation['values']['internal_code'], '')
            self.assertIsNone(operation['values']['default_hours'])
            self.assertEqual(operation['values']['aliases'], [])

    def test_57_records_repeat_without_new_courses_and_preserve_manual_decisions(self):
        rows = [source(f'SYN-{i:03}', f'Synthetic action {i:03}', f'CODE{i:03}' if i < 39 else '') for i in range(57)]
        self.assertEqual(approve(rows)['created'], 57)
        course = Course.objects.first()
        course.validation_notes = 'Manual reviewed note preserved'
        course.save()
        before = list(Course.objects.order_by('pk').values())
        result = approve(rows)
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['kept'], 57)
        self.assertEqual(list(Course.objects.order_by('pk').values()), before)
        self.assertEqual(Course.objects.filter(source_internal_code='').count(), 18)
        self.assertEqual(Course.objects.exclude(internal_code='').count(), 0)

    def test_history_integrity_including_five_exclusions(self):
        person = Employee.objects.create(number='SYN', name='Synthetic person')
        from .catalogue_diagnostic import EXCLUDED_SOURCE_KEYS
        keys = [f'SYN:{i}' for i in range(2052)] + list(EXCLUDED_SOURCE_KEYS)
        Training.objects.bulk_create([Training(employee=person, title='Historical original',
            start=date(2025, 1, 1), end=date(2025, 1, 2), days=2, attendance=1,
            hours_per_day=Decimal(7), source_key=key) for key in keys])
        before = list(Training.objects.order_by('pk').values())
        people = list(Employee.objects.values())
        approve([source()])
        self.assertEqual(list(Training.objects.order_by('pk').values()), before)
        self.assertEqual(list(Employee.objects.values()), people)
        self.assertEqual(Training.objects.count(), 2057)
        self.assertEqual(Training.objects.filter(source_key__in=EXCLUDED_SOURCE_KEYS).count(), 5)
        self.assertEqual(TrainingGroup.objects.count(), 0)
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(GroupSession.objects.count(), 0)

    def test_approval_tampering_and_destination_drift_are_rejected(self):
        rows = [source()]
        plan = simulate(rows)
        with self.assertRaises(CommandError):
            apply_plan(rows, plan, '', 'reviewer')
        tampered = copy.deepcopy(plan)
        tampered['operations'][0]['values']['validation_status'] = 'validated'
        with self.assertRaises(CommandError):
            apply_plan(rows, tampered, plan['plan_digest'], 'reviewer')
        with self.assertRaises(CommandError):
            apply_plan([source(title='Changed after simulation')], plan, plan['plan_digest'], 'reviewer')
        Course.objects.create(title='Concurrent addition')
        with self.assertRaises(CommandError):
            apply_plan(rows, plan, plan['plan_digest'], 'reviewer')
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_source_changes_and_duplicate_identity_title_code_conflicts(self):
        for rows in [[source(), source()],
                     [source(), source('OTHER', 'Other', 'syn')],
                     [source(), source('OTHER', ' SYNTHETIC   COURSE ', 'OTHER')]]:
            with self.subTest(rows=rows):
                plan = simulate(rows)
                self.assertTrue(plan['conflicts'])
                with self.assertRaises(CommandError):
                    apply_plan(rows, plan, plan['plan_digest'], 'reviewer')
        approve([source()])
        changed = source(title='Changed source')
        self.assertTrue(simulate([changed])['conflicts'])
        self.assertEqual(Course.objects.get().title, 'Synthetic course')

    def test_existing_unmapped_alias_blocks_automatic_adoption(self):
        Course.objects.create(title='Manually created', aliases=['Synthetic course'])
        self.assertTrue(simulate([source()])['conflicts'])

    def test_invalid_durations_and_empty_metadata_are_conflicts(self):
        for changes in [dict(days='0'), dict(days='1.5'), dict(hours='NaN'), dict(hours='-1'), dict(id=''), dict(title='')]:
            row = source()
            row.update(changes)
            self.assertTrue(simulate([row])['conflicts'], changes)

    def test_error_rolls_back_entire_batch_and_audit(self):
        rows = [source(), source('OTHER', 'Other', 'OTHER')]
        plan = simulate(rows)
        original = Course.save
        calls = []
        def fail_second(course, *args, **kwargs):
            calls.append(course.title)
            if len(calls) == 2:
                raise ValidationError('Synthetic error')
            return original(course, *args, **kwargs)
        with patch.object(Course, 'save', fail_second), self.assertRaises(CommandError):
            apply_plan(rows, plan, plan['plan_digest'], 'reviewer')
        self.assertEqual(Course.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_reader_and_command_only_use_catalogue_and_never_overwrite_plan(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.xlsx'
            plan_path = Path(directory) / 'plan.json'
            workbook = Workbook()
            workbook.active.title = 'CATALOGO'
            sheet = workbook.active
            sheet.append(['Synthetic catalogue'])
            sheet.append(['Synthetic metadata'])
            sheet.append(HEADERS)
            sheet.append(['SYN-1', 'Synthetic course', 'SYN', None, None, 7, 'Synthetic', 'Catálogo do plano'])
            workbook.create_sheet('UNUSED').append(['Synthetic private sentinel'])
            workbook.save(path)
            before = path.read_bytes()
            rows = read_catalogue(path)
            self.assertEqual(len(rows), 1)
            self.assertNotIn('private sentinel', json.dumps(rows))
            call_command('import_catalogue', str(path), '--simulate', '--plan', str(plan_path), stdout=StringIO())
            self.assertEqual(Course.objects.count(), 0)
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
            with self.assertRaises(CommandError):
                call_command('import_catalogue', str(path), '--simulate', '--plan', str(plan_path), stdout=StringIO())
            call_command('import_catalogue', str(path), '--apply', '--plan', str(plan_path),
                         '--approve', plan['plan_digest'], '--actor', 'synthetic reviewer', stdout=StringIO())
            self.assertEqual(Course.objects.count(), 1)
            self.assertEqual(path.read_bytes(), before)


class ConcurrentCatalogueImportTests(TransactionTestCase):
    def test_same_approved_plan_cannot_create_duplicates_concurrently(self):
        rows = [source()]
        plan = simulate(rows)
        def run():
            close_old_connections()
            try:
                return apply_plan(rows, plan, plan['plan_digest'], 'synthetic concurrent reviewer')['created']
            except CommandError:
                return 'rejected'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(results.count(1), 1, results)
        self.assertEqual(results.count('rejected'), 1, results)
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='CATALOGUE_IMPORT').count(), 1)

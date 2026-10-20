"""Synthetic 57-action catalogue; never opens production settings or data."""
import copy
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from .catalogue_duration_batch import READY, SOURCE_SYSTEM, apply, digest, simulate, simulate_reversal, snapshot
from .models import AuditEvent, Course, Employee, Enrollment, GroupSession, Training, TrainingGroup


class DurationBatchTests(TestCase):
    def setUp(self):
        for identifier in [f'AC-2026-{i:03}' for i in range(1,55)] + ['HC-RFTE','HC-RDSC','HC-FHDR']:
            days, hours = READY.get(identifier, (None, None))
            title = f'Synthetic action {identifier}'
            if identifier == 'AC-2026-040':
                title += ' 60 horas'
            metadata = {'days': str(days) if days else None,
                'hours': str(hours) if hours and identifier != 'AC-2026-040' else None,
                'title': title, 'origin': 'Synthetic document'}
            Course.objects.create(source_system=SOURCE_SYSTEM, source_action_id=identifier,
                title=title, default_days=days, default_hours=None,
                source_metadata=metadata, source_digest=digest(metadata),
                validation_status='blocked' if identifier == 'HC-RDSC' else 'pending',
                internal_code='WSFT' if identifier == 'AC-2026-051' else '',
                source_internal_code='WSFR' if identifier == 'AC-2026-051' else '')
        employee = Employee.objects.create(number='synthetic-batch', name='Synthetic')
        Training.objects.create(employee=employee, title='Synthetic independent historical record',
            days=5, hours_per_day=7, attendance=2,
            start=date(2025,1,1), end=date(2025,1,5))
        self.before = snapshot('default')
        self.history = list(Training.objects.values())

    def assert_no_write(self):
        self.assertEqual(snapshot('default'), self.before)
        self.assertFalse(AuditEvent.objects.exists())

    def test_simulation_only_selects_and_exact_scope(self):
        def read_only(execute, sql, params, many, context):
            if sql.split()[0].upper() in ('INSERT','UPDATE','DELETE','REPLACE','CREATE','DROP','ALTER'):
                self.fail(f'Simulation wrote SQL: {sql}')
            return execute(sql, params, many, context)
        with connection.execute_wrapper(read_only):
            plan = simulate()
        self.assertEqual(len(plan['operations']),29)
        self.assertEqual({op['source_action_id'] for op in plan['operations']},set(READY))
        self.assertNotIn('AC-2026-015',READY)
        self.assertNotIn('AC-2026-003',READY)
        self.assert_no_write()

    def test_apply_totals_audit_protected_fields_and_exclusions(self):
        plan = simulate()
        result = apply(plan,plan['plan_digest'],'Synthetic reviewer')
        self.assertEqual(result['updated'],29)
        after = {r['id']:r for r in snapshot('default')}
        for old in self.before:
            new = after[old['id']]
            if old['source_action_id'] not in READY:
                self.assertEqual(new,old)
                continue
            for field in old.keys()-{'default_days','default_hours','revision'}:
                self.assertEqual(new[field],old[field],field)
            course = Course.objects.get(pk=old['id'])
            days,hours = READY[course.source_action_id]
            self.assertEqual(course.total_hours,Decimal(days)*Decimal(hours))
            self.assertEqual(course.revision,2)
        self.assertEqual(list(Training.objects.values()),self.history)
        for model in [TrainingGroup,Enrollment,GroupSession]:
            self.assertEqual(model.objects.count(),0)
        self.assertEqual(AuditEvent.objects.filter(action='DURATION_ACTION').count(),29)
        event = json.loads(AuditEvent.objects.filter(action='DURATION_ACTION').first().detail)
        self.assertEqual(event['batch_id'],result['batch_id'])
        self.assertEqual(event['revision_after'],2)
        self.assertTrue(event['reason'])
        self.assertTrue(event['reference'])

    def test_failure_after_fourteen_actions_rolls_back_everything(self):
        plan = simulate()
        original = Course.full_clean
        count = 0
        def fail(course,*args,**kwargs):
            nonlocal count
            count += 1
            if count == 15:
                raise ValidationError('Synthetic midpoint failure')
            return original(course,*args,**kwargs)
        with patch.object(Course,'full_clean',fail),self.assertRaises(ValidationError):
            apply(plan,plan['plan_digest'],'Synthetic reviewer')
        self.assert_no_write()

    def test_stale_revision_or_values_reject_whole_plan(self):
        for changes in [{'revision':2},{'default_hours':Decimal('6')},{'default_days':3}]:
            plan = simulate()
            Course.objects.filter(source_action_id='AC-2026-006').update(**changes)
            changed = snapshot('default')
            with self.assertRaises(ValidationError):
                apply(plan,plan['plan_digest'],'Synthetic reviewer')
            self.assertEqual(snapshot('default'),changed)
            self.assertFalse(AuditEvent.objects.exists())
            Course.objects.filter(source_action_id='AC-2026-006').update(revision=1,default_hours=None,default_days=1)

    def test_changed_excluded_action_also_rejects_stale_plan(self):
        plan = simulate()
        Course.objects.filter(source_action_id='AC-2026-015').update(area='Synthetic concurrent change')
        with self.assertRaises(ValidationError):
            apply(plan,plan['plan_digest'],'Synthetic reviewer')
        self.assertFalse(AuditEvent.objects.exists())
        self.assertEqual(Course.objects.filter(default_hours__isnull=False).count(),0)

    def test_hash_destination_and_replay_protection(self):
        plan = simulate()
        for bad in [dict(plan,plan_digest='wrong'),dict(plan,database={'alias':'other'})]:
            with self.assertRaises(ValidationError):
                apply(bad,plan['plan_digest'],'Synthetic reviewer')
        tampered = copy.deepcopy(plan)
        tampered['operations'][0]['hours_after']='8'
        tampered['plan_digest']=digest({k:v for k,v in tampered.items() if k!='plan_digest'})
        with self.assertRaises(ValidationError):
            apply(tampered,tampered['plan_digest'],'Synthetic reviewer')
        self.assert_no_write()
        apply(plan,plan['plan_digest'],'Synthetic reviewer')
        with self.assertRaises(ValidationError):
            apply(plan,plan['plan_digest'],'Synthetic reviewer')
        self.assertEqual(AuditEvent.objects.filter(action='DURATION_ACTION').count(),29)

    def test_reversal_requires_new_plan_and_preserves_history(self):
        plan=simulate()
        result=apply(plan,plan['plan_digest'],'Synthetic reviewer')
        reversal=simulate_reversal(result['batch_id'])
        self.assertNotEqual(reversal['plan_digest'],plan['plan_digest'])
        apply(reversal,reversal['plan_digest'],'Synthetic reversal reviewer')
        after={r['id']:r for r in snapshot('default')}
        for old in self.before:
            expected=dict(old)
            if old['source_action_id'] in READY:
                expected['revision']=3
            self.assertEqual(after[old['id']],expected)
        self.assertEqual(list(Training.objects.values()),self.history)
        with self.assertRaises(ValidationError):
            apply(reversal,reversal['plan_digest'],'Synthetic reviewer')

    def test_reversal_refuses_to_overwrite_later_changes(self):
        plan=simulate()
        result=apply(plan,plan['plan_digest'],'Synthetic reviewer')
        Course.objects.filter(source_action_id='AC-2026-006').update(validation_notes='Later decision')
        with self.assertRaises(ValidationError):
            simulate_reversal(result['batch_id'])

    def test_midpoint_audit_failure_rolls_back_updates_and_audits(self):
        plan=simulate()
        original=AuditEvent.save
        def fail(event,*args,**kwargs):
            if event.action=='DURATION_ACTION' and AuditEvent.objects.filter(action='DURATION_ACTION').count()==14:
                raise RuntimeError('Synthetic audit failure')
            return original(event,*args,**kwargs)
        with patch.object(AuditEvent,'save',fail),self.assertRaises(RuntimeError):
            apply(plan,plan['plan_digest'],'Synthetic reviewer')
        self.assert_no_write()

    def test_simulation_refuses_changed_baseline_and_source_digest(self):
        for changes in [{'revision':2},{'default_days':2},{'source_digest':'modified'}]:
            with self.subTest(changes=changes):
                row=Course.objects.get(source_action_id='AC-2026-006')
                old={key:getattr(row,key) for key in changes}
                Course.objects.filter(pk=row.pk).update(**changes)
                with self.assertRaises(ValidationError):
                    simulate()
                Course.objects.filter(pk=row.pk).update(**old)
        self.assert_no_write()

    def test_command_plan_exclusive_and_requires_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic-plan.json'
            call_command('update_catalogue_duration','--simulate','--plan',str(path),stdout=StringIO())
            self.assert_no_write()
            with self.assertRaises(CommandError):
                call_command('update_catalogue_duration','--simulate','--plan',str(path),stdout=StringIO())
            with self.assertRaises(CommandError):
                call_command('update_catalogue_duration','--apply','--plan',str(path),stdout=StringIO())
            plan=json.loads(path.read_text(encoding='utf-8'))
            call_command('update_catalogue_duration','--apply','--plan',str(path),
                '--approve',plan['plan_digest'],'--actor','Synthetic reviewer',stdout=StringIO())
            self.assertEqual(AuditEvent.objects.filter(action='DURATION_ACTION').count(),29)


class ConcurrentDurationBatchTests(TransactionTestCase):
    setUp = DurationBatchTests.setUp

    def test_same_plan_can_only_commit_once(self):
        plan=simulate()
        def run():
            close_old_connections()
            try:
                return apply(plan,plan['plan_digest'],'Synthetic concurrent reviewer')['updated']
            except (ValidationError,DatabaseError):
                return 'rejected'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(results.count(29),1,results)
        self.assertEqual(results.count('rejected'),1,results)
        self.assertEqual(AuditEvent.objects.filter(action='DURATION_BATCH').count(),1)
        self.assertEqual(AuditEvent.objects.filter(action='DURATION_ACTION').count(),29)
        self.assertEqual(Course.objects.filter(revision=2).count(),29)
        self.assertEqual(list(Training.objects.values()),self.history)

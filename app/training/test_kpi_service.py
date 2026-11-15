from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase, TestCase
from datetime import date
from .kpi_service import Scope, aggregate_attendance
from .models import Employee, Training

D = Decimal
SCOPE = Scope(filters={}, period='All available history', time_basis='all_records')


def row(pk=1, employee=1, **changes):
    values = dict(pk=pk, employee_id=employee, group_id=None, course_id=None,
        source_key=None, status='Em Curso', attendance_confirmed=True,
        attendance=1, days=1, hours_per_day=D(7), group=None)
    values.update(changes)
    return SimpleNamespace(**values)


def calculate(rows, reference=(1, 2, 3)):
    return aggregate_attendance(rows, [SimpleNamespace(pk=pk) for pk in reference], scope=SCOPE)


class AggregateAttendanceTests(SimpleTestCase):
    def test_people_and_editions_have_distinct_units(self):
        result = calculate([row(group_id=10, hours_per_day=6), row(2, group_id=11)])
        self.assertEqual(result.metrics['people'].value, 1)
        self.assertEqual(result.metrics['participations'].value, 2)
        self.assertEqual(result.metrics['hours'].value, D(13))
        self.assertEqual(result.metrics['equivalent_days'].value, D('1.86'))
        self.assertEqual(result.metrics['coverage'].value, D('33.33'))
        self.assertEqual(result.metrics['hours_per_person'].value, D(13))

    def test_round_only_after_aggregation(self):
        for hours, expected in [(6,'0.86'), (7,'1.00')]:
            with self.subTest(hours=hours):
                self.assertEqual(calculate([row(hours_per_day=hours)]).metrics['equivalent_days'].value, D(expected))
        result = calculate([row(hours_per_day=6),row(2,2,hours_per_day=6)])
        self.assertEqual(result.metrics['equivalent_days'].value, D('1.71'))

    def test_attendance_is_weighted_by_hours_and_counts_absences(self):
        result = calculate([row(hours_per_day=6),row(2,2,attendance=0)])
        rate=result.metrics['attendance']
        self.assertEqual((rate.value,rate.numerator,rate.denominator), (D('46.15'),D(6),D(13)))
        self.assertEqual(result.metrics['people'].value,1)

    def test_confirmed_zero_is_measurable(self):
        result=calculate([row(attendance=0)])
        self.assertEqual(result.metrics['hours'].value,0)
        self.assertEqual(result.metrics['attendance'].value,0)
        self.assertEqual(result.metrics['attendance'].status,'valid')
        self.assertEqual(result.metrics['participations'].value,0)
        self.assertIsNone(result.metrics['hours_per_person'].value)

    def test_unconfirmed_zero_is_not_an_absence(self):
        result=calculate([row(attendance=0,attendance_confirmed=False)])
        self.assertIsNone(result.metrics['hours'].value)
        self.assertIsNone(result.metrics['attendance'].denominator)
        self.assertEqual(result.metrics['people'].status,'to_verify')

    def test_unverified_positive_excluded_from_both_sides(self):
        result=calculate([row(),row(2,2,attendance_confirmed=False)])
        rate=result.metrics['attendance']
        self.assertEqual((rate.numerator,rate.denominator),(D(7),D(7)))
        self.assertEqual(rate.status,'partial')
        self.assertEqual(rate.reasons,{'unverified_attendance':1})
        self.assertEqual(result.metrics['people'].value,1)

    def test_unknown_duration_preserves_people_but_withholds_average(self):
        result=calculate([row(),row(2,2,hours_per_day=None)])
        self.assertEqual(result.metrics['people'].value,2)
        self.assertEqual(result.metrics['hours'].value,7)
        self.assertEqual(result.metrics['hours'].status,'partial')
        self.assertIsNone(result.metrics['hours_per_person'].value)
        self.assertEqual(result.metrics['hours_per_person'].status,'to_verify')

    def test_unknown_planned_days_do_not_erase_known_hours(self):
        result=calculate([row(days=None)])
        self.assertEqual(result.metrics['hours'].value,7)
        self.assertIsNone(result.metrics['attendance'].value)
        self.assertEqual(result.metrics['attendance'].reasons,{'missing_duration':1})

    def test_missing_attendance_never_becomes_zero(self):
        result=calculate([row(attendance=None)])
        self.assertIsNone(result.metrics['hours'].value)
        self.assertEqual(result.metrics['people'].status,'to_verify')

    def test_empty_universe_and_no_records(self):
        result=calculate([],reference=())
        self.assertIsNone(result.metrics['coverage'].value)
        self.assertIsNone(result.metrics['attendance'].value)
        self.assertIsNone(result.metrics['hours'].value)
        self.assertEqual(result.metrics['coverage'].status,'no_data')

    def test_registry_not_limited_to_participants(self):
        result=calculate([row()],reference=(1,1,2,3))
        self.assertEqual(result.metrics['coverage'].denominator,3)
        self.assertEqual(result.metrics['coverage'].value,D('33.33'))

    def test_duplicate_input_object_is_counted_once(self):
        first=row()
        result=calculate([first,row()])
        self.assertEqual(result.duplicate_inputs,1)
        self.assertEqual(result.selected_participations,1)
        self.assertEqual(result.metrics['hours'].value,7)
        self.assertEqual(first.attendance,1)

    def test_conflicting_versions_are_excluded(self):
        result=calculate([row(),row(attendance=0)])
        self.assertEqual(result.metrics['hours'].status,'inconsistent')
        self.assertEqual(result.issues[1],('conflicting_versions',))

    def test_duplicate_canonical_identity_is_not_silently_merged(self):
        for changes in [{'group_id':10},{'source_key':'legacy:1'}]:
            with self.subTest(changes=changes):
                result=calculate([row(**changes),row(2,**changes)])
                self.assertEqual(result.metrics['people'].value,0)
                self.assertEqual(result.metrics['hours'].excluded,2)
                self.assertIn('duplicate_identity',result.issues[1])

    def test_distinct_legacy_records_are_preserved(self):
        result=calculate([row(),row(2)])
        self.assertEqual(result.legacy_without_group,2)
        self.assertEqual(result.metrics['participations'].value,2)
        self.assertEqual(result.metrics['hours'].value,14)

    def test_distinct_identities_not_names(self):
        result=calculate([row(),row(2,2)])
        self.assertEqual(result.metrics['people'].value,2)

    def test_planned_records_not_attendance(self):
        result=calculate([row(status='Planeado',attendance=0)])
        self.assertIsNone(result.metrics['hours'].value)
        self.assertEqual(result.metrics['hours'].reasons,{'planned':1})
        result=calculate([row(status='Planeado')])
        self.assertIn('planned_with_attendance',result.issues[1])

    def test_invalid_values_are_reported(self):
        for changes in [dict(attendance=-1),dict(attendance=2),dict(attendance='NaN'),
                        dict(days=0),dict(hours_per_day=0),dict(hours_per_day='Infinity'),
                        dict(employee_id=None),dict(course_id=1,group=SimpleNamespace(course_id=2))]:
            with self.subTest(changes=changes):
                result=calculate([row(**changes)])
                self.assertEqual(result.metrics['hours'].status,'inconsistent')
                self.assertEqual(result.metrics['hours'].excluded,1)

    def test_exclusion_total_counts_record_once(self):
        result=calculate([row(days=0,hours_per_day=-1,attendance=2)])
        self.assertEqual(result.metrics['attendance'].excluded,1)
        self.assertGreater(len(result.metrics['attendance'].reasons),1)

    def test_scope_mismatch_not_clamped_to_100(self):
        result=calculate([row(),row(2,2)],reference=(1,))
        self.assertEqual(result.metrics['coverage'].value,200)
        self.assertEqual(result.metrics['coverage'].status,'inconsistent')
        self.assertEqual(result.metrics['coverage'].reasons['outside_reference_people'],1)

    def test_completed_status_not_required(self):
        result=calculate([row(status='Desistência'),row(2,2,status='Em Curso')])
        self.assertEqual(result.metrics['people'].value,2)
        self.assertNotIn('completion',result.metrics)

    def test_scope_retained_and_daily_allocation_rejected(self):
        self.assertEqual(calculate([row()]).scope,SCOPE)
        with self.assertRaises(ValueError):
            aggregate_attendance([row()],[],scope=replace(SCOPE,time_basis='session_date'))

    def test_unsaved_identities_rejected(self):
        with self.assertRaises(ValueError):
            calculate([row(pk=None)])
        with self.assertRaises(ValueError):
            calculate([],reference=(None,))


class AggregateORMTests(TestCase):
    def test_model_instances_no_writes_or_hidden_queries(self):
        employee=Employee.objects.create(number='KPI-01',name='KPI test')
        training=Training.objects.create(employee=employee,title='KPI test',start=date(2026,1,1),
            end=date(2026,1,1),attendance=1,days=1,hours_per_day=6,attendance_confirmed=True)
        with self.assertNumQueries(0):
            result=aggregate_attendance([training],[employee],scope=SCOPE)
        self.assertEqual(result.metrics['hours'].value,D(6))
        training.refresh_from_db()
        self.assertEqual(training.version,1)

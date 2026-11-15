"""Read-only aggregate attendance KPIs. No daily allocation or academic inference.

Callers supply the selected participations AND the independent employee registry
for the same organisational scope. Search/pagination must not trim either input.
Dates are selection metadata: this service never invents daily attendance.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class Scope:
    filters: Mapping[str, Any]
    period: str
    time_basis: str  # e.g. training_start or training_overlap; never session_date here
    reference: str = 'current_registry'


@dataclass(frozen=True)
class Metric:
    value: Decimal | int | None
    unit: str
    status: str  # valid, partial, to_verify, no_data, inconsistent
    numerator: Decimal | int | None
    denominator: Decimal | int | None
    included: int
    excluded: int
    reasons: Mapping[str, int]


@dataclass(frozen=True)
class Summary:
    metrics: Mapping[str, Metric]
    scope: Scope
    source: str
    selected_participations: int
    duplicate_inputs: int
    legacy_without_group: int
    issues: Mapping[Any, tuple[str, ...]]


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _rounded(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def _signature(row):
    return tuple(getattr(row, key, None) for key in (
        'employee_id', 'group_id', 'course_id', 'source_key', 'status',
        'attendance_confirmed', 'attendance', 'days', 'hours_per_day'))


def aggregate_attendance(rows: Iterable, employees: Iterable, *, scope: Scope) -> Summary:
    """Calculate KPIs 1–6 from persisted Training records without writing them.

    Missing hours preserve confirmed people/counts. A partial hours-per-person
    average is withheld. Counts of unknown attendance are not confirmed absences.
    Duplicate object instances of one PK count once; conflicting versions or
    duplicate canonical identities are excluded and reported, never merged.
    """
    if scope.time_basis not in ('training_start', 'training_overlap', 'all_records'):
        raise ValueError('Aggregate records require an explicit non-daily time basis.')
    reference_ids = set()
    for employee in employees:
        if employee.pk is None:
            raise ValueError('Reference employees must have persisted identities.')
        reference_ids.add(employee.pk)
    unique, issues, duplicates = {}, defaultdict(set), 0
    for row in rows:
        if row.pk is None:
            raise ValueError('Participations must have persisted identities.')
        if row.pk in unique:
            duplicates += 1
            if _signature(row) != _signature(unique[row.pk]):
                issues[row.pk].add('conflicting_versions')
        else:
            unique[row.pk] = row
    identities = defaultdict(list)
    for pk, row in unique.items():
        if row.group_id is not None:
            identities[('group', row.employee_id, row.group_id)].append(pk)
        if row.source_key:
            identities[('source', row.source_key)].append(pk)
    for ids in identities.values():
        if len(ids) > 1:
            for pk in ids:
                issues[pk].add('duplicate_identity')

    positive, known_attendance, hour_values, pairs = {}, set(), {}, {}
    exclusions = {name: defaultdict(set) for name in ('counts', 'hours', 'attendance')}
    inconsistent = set()
    planned = set()
    for pk, row in unique.items():
        problems = set(issues[pk])
        attendance, days, hours = map(_decimal, (row.attendance, row.days, row.hours_per_day))
        if row.employee_id is None:
            problems.add('missing_employee_identity')
        if attendance is not None and (attendance < 0 or attendance != attendance.to_integral_value()):
            problems.add('invalid_attendance')
        if row.attendance is not None and attendance is None:
            problems.add('invalid_attendance')
        if row.days is not None and (days is None or days < 1 or days != days.to_integral_value()):
            problems.add('invalid_duration')
        if row.hours_per_day is not None and (hours is None or not Decimal('.5') <= hours <= 24):
            problems.add('invalid_duration')
        if attendance is not None and days is not None and attendance > days:
            problems.add('attendance_exceeds_planned')
        # Inspect an already-loaded group only; do not cause hidden ORM queries.
        state = getattr(row, '_state', None)
        group = state.fields_cache.get('group') if state else getattr(row, 'group', None)
        if group is not None and row.course_id is not None and group.course_id != row.course_id:
            problems.add('group_course_mismatch')
        if row.status == 'Planeado' and attendance is not None and attendance > 0:
            problems.add('planned_with_attendance')
        if problems:
            inconsistent.add(pk)
            issues[pk].update(problems)
            for bucket in exclusions.values():
                bucket[pk].update(problems)
            continue
        if row.status == 'Planeado':
            planned.add(pk)
            for bucket in exclusions.values():
                bucket[pk].add('planned')
            continue
        if row.attendance_confirmed is not True or attendance is None:
            reason = 'unverified_attendance' if row.attendance_confirmed is not True else 'missing_attendance'
            for bucket in exclusions.values():
                bucket[pk].add(reason)
            continue
        known_attendance.add(pk)
        if attendance > 0:
            positive[pk] = row.employee_id
        if hours is None:
            exclusions['hours'][pk].add('missing_duration')
        else:
            hour_values[pk] = attendance * hours
        if hours is None or days is None:
            exclusions['attendance'][pk].add('missing_duration')
        else:
            pairs[pk] = (attendance * hours, days * hours)

    def metric(value, unit, bucket, included, numerator=None, denominator=None, status=None):
        excluded = exclusions[bucket]
        reasons = Counter(reason for reason_set in excluded.values() for reason in reason_set)
        uncertain = set(excluded) - planned
        if status is None:
            status = 'inconsistent' if inconsistent else (
                'partial' if uncertain and value is not None and included else
                'to_verify' if uncertain else 'no_data' if value is None else 'valid')
        return Metric(value, unit, status, numerator, denominator, included, len(excluded), dict(reasons))

    people = set(positive.values())
    total_hours = sum(hour_values.values(), Decimal(0)) if hour_values else None
    present_hours = sum((pair[0] for pair in pairs.values()), Decimal(0)) if pairs else None
    planned_hours = sum((pair[1] for pair in pairs.values()), Decimal(0)) if pairs else None
    metrics = {
        'hours': metric(total_hours, 'participant_hours', 'hours', len(hour_values)),
        'equivalent_days': metric(_rounded(total_hours / 7) if total_hours is not None else None,
            'seven_hour_equivalents', 'hours', len(hour_values), total_hours, 7),
        'participations': metric(len(positive), 'participations', 'counts', len(known_attendance)),
        'people': metric(len(people), 'people', 'counts', len(known_attendance)),
        'coverage': metric(_rounded(Decimal(len(people)) * 100 / len(reference_ids)) if reference_ids else None,
            'percent', 'counts', len(known_attendance), len(people), len(reference_ids)),
        'attendance': metric(_rounded(present_hours * 100 / planned_hours) if planned_hours else None,
            'percent', 'attendance', len(pairs), present_hours, planned_hours),
    }
    # The registry is independent of training filters. Never hide a mismatch by
    # dropping people from the numerator or clamping a rate at 100%.
    if people - reference_ids:
        old = metrics['coverage']
        metrics['coverage'] = Metric(old.value, old.unit, 'inconsistent', old.numerator,
            old.denominator, old.included, old.excluded,
            {**old.reasons, 'outside_reference_people': len(people - reference_ids)})
    missing_positive_hours = set(positive) - set(hour_values)
    average = total_hours / len(people) if people and total_hours is not None and not missing_positive_hours else None
    metrics['hours_per_person'] = metric(_rounded(average) if average is not None else None,
        'hours_per_person', 'hours', len(hour_values), total_hours, len(people),
        status='inconsistent' if inconsistent else 'to_verify' if missing_positive_hours else None)
    return Summary(metrics, scope, 'Training aggregate attendance; uniform hours per day',
        len(unique), duplicates, sum(row.group_id is None for row in unique.values()),
        {pk: tuple(sorted(reasons)) for pk, reasons in issues.items() if reasons})

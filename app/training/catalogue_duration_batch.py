"""Explicitly reviewed duration batches. No Excel, history or group access."""
import hashlib
import json
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connections, transaction

from .models import AuditEvent, Course


SOURCE_SYSTEM = 'epal-proagua-catalogo'
# Exact scope reviewed in Phase 4: (existing days, proposed daily hours).
READY = {
    '006': (1, 7), '007': (2, 7), '008': (2, 7), '010': (2, 7),
    '011': (10, 7), '013': (5, 7), '014': (3, 7), '018': (2, 6),
    '020': (3, 7), '021': (3, 7), '025': (1, 6), '027': (2, 7),
    '028': (3, 7), '029': (2, 7), '031': (3, 6), '032': (3, 6),
    '033': (3, 7), '034': (3, 7), '036': (2, 7), '038': (2, 7),
    '039': (2, 7), '040': (10, 6), '041': (3, 7), '044': (2, 7),
    '046': (5, 7), '047': (2, 7), '048': (2, 6), '050': (5, 7),
    '051': (1, 7),
}
READY = {f'AC-2026-{key}': value for key, value in READY.items()}


def canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def snapshot(database):
    return canonical(list(Course.objects.using(database).order_by('pk').values()))


def destination(database):
    config = connections[database].settings_dict
    return {'alias': database, 'engine': config['ENGINE'], 'name': str(config['NAME'])}


def operations_for(rows):
    identities = {f'AC-2026-{i:03}' for i in range(1, 55)} | {'HC-RFTE', 'HC-RDSC', 'HC-FHDR'}
    if len(rows) != 57 or {r['source_action_id'] for r in rows} != identities or any(
            r['source_system'] != SOURCE_SYSTEM for r in rows):
        raise ValidationError('Expected exactly 57 catalogue actions.')
    operations = []
    for identifier, (days, hours) in READY.items():
        matches = [r for r in rows if r['source_system'] == SOURCE_SYSTEM
                   and r['source_action_id'] == identifier]
        if len(matches) != 1:
            raise ValidationError(f'Missing or ambiguous source identity: {identifier}')
        row = matches[0]
        if row['revision'] != 1 or row['default_days'] != days or row['default_hours'] is not None:
            raise ValidationError(f'Unexpected revision or existing duration: {identifier}')
        try:
            metadata = row['source_metadata']
            if digest(metadata) != row['source_digest'] or int(metadata['days']) != days:
                raise ValueError
            if identifier != 'AC-2026-040':
                if Decimal(str(metadata['hours'])) != hours:
                    raise ValueError
            elif metadata['hours'] is not None or '60 horas' not in metadata['title']:
                raise ValueError
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise ValidationError(f'Source duration or digest differs: {identifier}')
        operations.append({'pk': row['id'], 'source_action_id': identifier,
            'source_system': SOURCE_SYSTEM, 'revision': row['revision'],
            'days_before': days, 'hours_before': None, 'days_after': days,
            'hours_after': str(Decimal(hours)), 'total_hours': str(Decimal(days) * hours),
            'reference': 'CATALOGO.days/hours; TURMAS/FASES Hours/day' if identifier != 'AC-2026-040'
                         else 'CATALOGO.title: 60 horas; CATALOGO.days: 10',
            'reason': 'Phase 4 reviewed daily duration' if identifier != 'AC-2026-040'
                      else 'Explicit accumulated total divided by days: 60 / 10'})
    return operations


def seal(plan):
    return {**plan, 'plan_digest': digest(plan)}


def simulate(database='default'):
    # A consistent read snapshot, with no INSERT/UPDATE or audit during simulation.
    with transaction.atomic(using=database):
        rows = snapshot(database)
        return seal({'version': 1, 'kind': 'duration-update', 'database': destination(database),
                     'before': rows, 'operations': operations_for(rows)})


def simulate_reversal(batch_id, database='default'):
    with transaction.atomic(using=database):
        event = AuditEvent.objects.using(database).get(action='DURATION_BATCH', detail__contains=batch_id)
        detail = json.loads(event.detail)
        if detail['batch_id'] != batch_id or detail['kind'] != 'duration-update':
            raise ValidationError('Not an original duration update batch.')
        rows = snapshot(database)
        if rows != detail['after']:
            raise ValidationError('Catalogue changed after batch; reversal requires a new review.')
        old = {r['id']: r for r in detail['before']}
        operations = []
        for op in detail['operations']:
            current = next(r for r in rows if r['id'] == op['pk'])
            previous = old[op['pk']]
            operations.append({**op, 'revision': current['revision'],
                'days_before': current['default_days'], 'hours_before': current['default_hours'],
                'days_after': previous['default_days'], 'hours_after': previous['default_hours'],
                'total_hours': None if previous['default_hours'] is None else
                    str(Decimal(previous['default_days']) * Decimal(previous['default_hours'])),
                'reason': f'Controlled reversal of batch {batch_id}', 'reference': str(event.pk)})
        return seal({'version': 1, 'kind': 'duration-reversal', 'database': destination(database),
                     'reverses': batch_id, 'before': rows, 'operations': operations})


def apply(plan, approval, actor, database='default'):
    if not actor.strip() or len(actor) > 150:
        raise ValidationError('A named actor of at most 150 characters is required.')
    unsigned = {k: v for k, v in plan.items() if k != 'plan_digest'}
    if not approval or approval != plan.get('plan_digest') or digest(unsigned) != approval:
        raise ValidationError('Plan approval hash differs.')
    if plan['database'] != destination(database):
        raise ValidationError('Destination database differs.')
    batch_id = str(uuid.uuid4())
    with transaction.atomic(using=database):
        # First write serializes SQLite writers; any subsequent failure rolls it back.
        header = AuditEvent.objects.using(database).create(actor=actor, action='DURATION_BATCH', detail='pending')
        if snapshot(database) != plan['before']:
            raise ValidationError('Catalogue snapshot changed; no updates applied.')
        expected = simulate(database) if plan['kind'] == 'duration-update' else simulate_reversal(plan['reverses'], database)
        if expected != plan:
            raise ValidationError('Plan differs from reviewed batch specification.')
        for op in plan['operations']:
            course = Course.objects.using(database).get(pk=op['pk'])
            course.default_days = op['days_after']
            course.default_hours = None if op['hours_after'] is None else Decimal(op['hours_after'])
            course.full_clean()
            changed = Course.objects.using(database).filter(pk=op['pk'],
                source_system=op['source_system'], source_action_id=op['source_action_id'],
                revision=op['revision'], default_days=op['days_before'],
                default_hours=op['hours_before']).update(default_days=course.default_days,
                    default_hours=course.default_hours, revision=op['revision'] + 1)
            if changed != 1:
                raise ValidationError('Concurrent duration change; whole batch rolled back.')
            AuditEvent.objects.using(database).create(actor=actor, action='DURATION_ACTION',
                detail=json.dumps(canonical({'batch_id': batch_id, 'plan_digest': approval,
                    'kind': plan['kind'], 'revision_after': op['revision'] + 1, **op}), ensure_ascii=False))
        after = snapshot(database)
        header.detail = json.dumps(canonical({'batch_id': batch_id, 'kind': plan['kind'],
            'plan_digest': approval, 'before': plan['before'], 'after': after,
            'operations': plan['operations'], 'reverses': plan.get('reverses')}), ensure_ascii=False)
        header.save(using=database, update_fields=['detail'])
    return {'batch_id': batch_id, 'plan_digest': approval, 'updated': len(plan['operations'])}

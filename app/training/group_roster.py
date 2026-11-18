"""Shared group membership and display summaries. Read-only, explicit bindings only."""
import json
from types import SimpleNamespace
from django.db.models import Q
from .models import Training, Enrollment, AuditEvent


def source_keys(group_ids):
    keys = {pk: set() for pk in group_ids}
    for event in AuditEvent.objects.filter(action='IMPORT_GROUPS_70_V1').only('detail'):
        try:
            payload = json.loads(event.detail)
            if not isinstance(payload, dict) or payload.get('group_id') not in keys:
                continue
            raw = (payload.get('source') or {}).get('IDs de participação', '')
            if isinstance(raw, str):
                keys[payload['group_id']].update('pilar3b:' + value.strip() for value in raw.split('|') if value.strip())
        except (ValueError, TypeError, AttributeError):
            continue
    return keys


def group_trainings(group):
    keys = source_keys([group.pk])[group.pk]
    return Training.objects.filter(Q(group_id=group.pk) | Q(source_key__in=keys)).select_related('employee').order_by('employee__name', 'pk')


def summaries(groups):
    groups = list(groups)
    result = {g.pk: {'group': g, 'members': {}, 'trainings': {}, 'active_count': 0} for g in groups}
    if not result:
        return {}
    for en in Enrollment.objects.filter(group_id__in=result, state='Activa').select_related('employee'):
        item = result[en.group_id]
        item['active_count'] += 1
        item['members'][en.employee_id] = SimpleNamespace(employee=en.employee, enrollment=en, training=None)
    keys = source_keys(result)
    reverse = {}
    for pk, values in keys.items():
        for key in values:
            reverse.setdefault(key, set()).add(pk)
    trainings = Training.objects.filter(Q(group_id__in=result) | Q(source_key__in=reverse)).select_related('employee').order_by('pk')
    for training in trainings:
        targets = set(reverse.get(training.source_key, set()))
        if training.group_id in result:
            targets.add(training.group_id)
        for pk in targets:
            item = result[pk]
            item['trainings'][training.pk] = training
            member = item['members'].setdefault(training.employee_id, SimpleNamespace(employee=training.employee, enrollment=None, training=None))
            # Retain all source records internally; show one person in the roster.
            if member.training is None or (training.group_id == pk and member.training.group_id != pk):
                member.training = training
    for pk, item in result.items():
        records = list(item['trainings'].values())
        item['members'] = sorted(item['members'].values(), key=lambda m: (m.employee.name.casefold(), m.employee.number))
        item['participant_count'] = len(item['members'])
        item['participation_count'] = len(records)
        item['completed_count'] = len({t.employee_id for t in records if t.status == 'Concluído'})
        item['verified_present_count'] = len({t.employee_id for t in records if t.attendance_confirmed and t.attendance > 0 and t.status != 'Planeado'})
        verified = {t.employee_id for t in records if t.attendance_confirmed and t.status != 'Planeado'}
        item['attendance_pending_count'] = item['participant_count'] - len(verified)
        item['historical_only'] = bool(keys[pk] and records and not item['active_count'])
        item['display_state'] = 'Registo histórico' if item['historical_only'] and item['group'].state != 'Cancelada' else item['group'].state
    return result


def attendance_rows(group):
    """Same distinct people as the on-screen roster; no presence is inferred."""
    return summaries([group])[group.pk]['members']

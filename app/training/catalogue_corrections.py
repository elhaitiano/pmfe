"""Reviewed catalogue corrections; no writes to history or group models."""
import json
import uuid
from decimal import Decimal

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .catalogue_duration_batch import canonical, digest
from .models import AuditEvent, Course

SALT = 'proagua.catalogue-correction.v1'
MAX_AGE = 1200
ACTION = 'CATALOGUE_CORRECTION'
BATCH_ACTION = 'CATALOGUE_CORRECTION_BATCH'

# Each documented decision has a closed scope. Security and application remain shared.
RULES = {
    'AC-2026-015': {
        'days':5, 'hours':'6', 'source_hours':'7',
        'warning':'O título indica 30 horas em cinco dias; a origem contém 7. As 17 participações históricas de 35 horas não serão alteradas. Não se afirma que um erro de digitação foi comprovado.',
        'evidence': {'source_hours':'7', 'historical_participations':17,
            'historical_planned_hours_each':'35',
            'basis':'Decisão expressa sobre o padrão; histórico não corrigido.'}},
    'AC-2026-037': {
        'days':2, 'hours':'7', 'source_hours':'7',
        'warning':'O padrão proposto é de 2 dias × 7 horas = 14 horas. Existe uma edição documental de três dias com horas desconhecidas. Não se atribuem horas a essa edição nem se afirma que esteja errada.',
        'evidence': {'source_hours':'7', 'historical_participations':16,
            'historical_planned_hours_each':'14', 'other_edition_days':3,
            'other_edition_hours':None, 'basis':'Padrão declarado no catálogo; variante documental preservada.'}},
    'AC-2026-004': {
        'days':10, 'hours':'6', 'source_hours':'6',
        'warning':'O padrão proposto do COP1 é de 10 dias × 6 horas = 60 horas. Existem fases de 7 dias × 6 horas = 42 horas. A decomposição integral do programa de 60 horas não está comprovada; não se presume uma Fase 1 de três dias. As durações históricas são preservadas.',
        'evidence': {'source_hours':'6', 'phase_days':7, 'phase_total_hours':'42',
            'full_program_decomposition_confirmed':False,
            'basis':'Padrão declarado em CATALOGO/TURMAS; fases e participações não recalculadas.'}},
    'AC-2026-005': {
        'days':10, 'hours':'6', 'source_hours':'6',
        'warning':'O padrão proposto do COP2 é de 10 dias × 6 horas = 60 horas. Existem fases de 7 dias × 6 horas = 42 horas. A decomposição integral do programa de 60 horas não está comprovada; não se presume uma Fase 1 de três dias. As durações históricas são preservadas.',
        'evidence': {'source_hours':'6', 'phase_days':7, 'phase_total_hours':'42',
            'full_program_decomposition_confirmed':False,
            'basis':'Padrão declarado em CATALOGO/TURMAS; fases e participações não recalculadas.'}},
    'AC-2026-026': {
        'days':3, 'hours':'6', 'source_hours':'6',
        'warning':'O padrão proposto de Saber Estar é de 3 dias × 6 horas = 18 horas. Existem 76 participações históricas de seis horas. São execuções distintas do padrão proposto; não se afirma que estejam erradas nem se somam para reconstruir uma edição de três dias.',
        'evidence': {'source_hours':'6', 'historical_participations':76,
            'historical_planned_hours_each':'6',
            'basis':'Decisão sobre o padrão declarado no catálogo; execuções de um dia preservadas.'}},
}


def authorise(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff or not user.has_perm('training.change_course'):
        raise PermissionDenied('Sem permissão para corrigir dados do catálogo.')


def in_scope(course):
    return course.source_system == 'epal-proagua-catalogo' and course.source_action_id in RULES


def course_state(course):
    return canonical(Course.objects.using(course._state.db or 'default').values().get(pk=course.pk))


def total(days, hours):
    return None if days is None or hours is None else str(Decimal(days) * Decimal(hours))


def foundation(data):
    reason = str(data.get('justification', '')).strip()
    reference = str(data.get('reference', '')).strip()
    if not reason or not reference or len(reason) > 4000 or len(reference) > 1000:
        raise ValidationError('Indique uma justificação e referência documental válidas.')
    return reason, reference


def package(course, user, data, mode='correct', original_event=None):
    authorise(user)
    if not in_scope(course):
        raise PermissionDenied('Acção fora do âmbito aprovado. GIRH permanece suspensa.')
    reason, reference = foundation(data)
    before = course_state(course)
    rule = RULES[course.source_action_id]
    if before['revision'] != data.get('revision'):
        raise ValidationError('A revisão mudou. Reabra a acção e reveja a proposta.')
    if mode == 'correct':
        if before['default_days'] != rule['days'] or before['default_hours'] is not None or before['validation_status'] != 'pending':
            raise ValidationError('O estado actual não corresponde à correcção inicial aprovada.')
        metadata = before['source_metadata']
        if metadata.get('days') != str(rule['days']) or metadata.get('hours') != rule['source_hours'] or digest(metadata) != before['source_digest']:
            raise ValidationError('Os metadados originais divergem da decisão documentada.')
        if (data.get('correction_type') != 'documented_standard' or
                data.get('days') != rule['days'] or data.get('hours') != Decimal(rule['hours'])):
            raise ValidationError('Os valores divergem da decisão documentada autorizada para esta acção.')
        days, hours = rule['days'], Decimal(rule['hours'])
    else:
        if original_event.action != ACTION:
            raise ValidationError('Evento de correcção inválido.')
        original = json.loads(original_event.detail)
        if original['mode'] != 'correct' or original['course_id'] != course.pk or original['after'] != before:
            raise ValidationError('A reversão não pode sobrepor alterações posteriores.')
        days, hours = original['before']['default_days'], original['before']['default_hours']
    after = dict(before, default_days=days,
                 default_hours=None if hours is None else str(Decimal(hours).quantize(Decimal('.01'))),
                 revision=before['revision'] + 1)
    payload = {'version': 1, 'mode': mode, 'user_id': user.pk, 'course_id': course.pk,
        'database': course._state.db or 'default', 'source_system': course.source_system,
        'source_action_id': course.source_action_id, 'before': before, 'after': after,
        'total_before': total(before['default_days'], before['default_hours']),
        'total_after': total(days, hours), 'correction_type': 'documented_standard',
        'justification': reason, 'reference': reference,
        'original_event': original_event.pk if original_event else None,
        'nonce': str(uuid.uuid4()),
        'evidence': rule['evidence']}
    return payload, signing.dumps(payload, salt=SALT, compress=True)


def read_proposal(token, user, course, mode, original_event_id=None):
    authorise(user)
    if not in_scope(course):
        raise PermissionDenied('Acção fora do âmbito aprovado.')
    payload = signing.loads(token, salt=SALT, max_age=MAX_AGE)
    if (payload['user_id'] != user.pk or payload['course_id'] != course.pk or
            payload['database'] != (course._state.db or 'default') or payload['mode'] != mode or
            payload['original_event'] != original_event_id or payload['version'] != 1):
        raise ValidationError('Proposta inválida para este utilizador, acção ou operação.')
    return payload


def confirm(token, user, course, mode='correct', original_event_id=None):
    payload = read_proposal(token, user, course, mode, original_event_id)
    database = course._state.db or 'default'
    with transaction.atomic(using=database):
        # First write serializes SQLite writers, including repeat confirmations.
        header = AuditEvent.objects.using(database).create(actor=user.get_username(), action=BATCH_ACTION, detail='pending')
        current = Course.objects.using(database).get(pk=course.pk)
        if course_state(current) != payload['before']:
            raise ValidationError('A acção mudou ou esta proposta já foi confirmada. Reveja os dados.')
        original = AuditEvent.objects.using(database).get(pk=original_event_id) if original_event_id else None
        # Recompute all permitted changes and documentary rules from the signed values.
        checked, _ = package(current, user, {'revision':payload['before']['revision'],
            'days':payload['after']['default_days'],
            'hours':None if payload['after']['default_hours'] is None else Decimal(payload['after']['default_hours']),
            'correction_type':payload['correction_type'], 'justification':payload['justification'],
            'reference':payload['reference']}, mode, original)
        for key in payload.keys() - {'nonce'}:
            if payload[key] != checked[key]:
                raise ValidationError('A proposta diverge da especificação permitida.')
        current.default_days = payload['after']['default_days']
        current.default_hours = None if payload['after']['default_hours'] is None else Decimal(payload['after']['default_hours'])
        current.full_clean()
        if Course.objects.using(database).filter(pk=current.pk, revision=payload['before']['revision']).update(
                default_days=current.default_days, default_hours=current.default_hours,
                revision=payload['after']['revision']) != 1:
            raise ValidationError('Alteração concorrente; correcção anulada.')
        if course_state(current) != payload['after']:
            raise ValidationError('Campos não autorizados divergentes; correcção anulada.')
        detail = {**payload, 'proposal_digest': digest(payload), 'user_id':user.pk,
                  'actor':user.get_username(), 'batch_event_id':header.pk}
        event = AuditEvent.objects.using(database).create(actor=user.get_username(), action=ACTION,
            detail=json.dumps(detail, ensure_ascii=False))
        header.detail = json.dumps({'course_id':course.pk, 'individual_event_id':event.pk,
            'mode':mode, 'proposal_digest':digest(payload), 'nonce':payload['nonce']}, ensure_ascii=False)
        header.save(using=database, update_fields=['detail'])
    return event


def correction_history(course):
    result = []
    for event in AuditEvent.objects.using(course._state.db or 'default').filter(action=ACTION).order_by('-pk'):
        detail = json.loads(event.detail)
        if detail['course_id'] == course.pk:
            result.append({'event':event, 'detail':detail,
                'reversible':detail['mode'] == 'correct' and detail['after'] == course_state(course)})
    return result

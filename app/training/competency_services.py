"""Transactional lifecycle and reproducible aggregates without individual disclosure."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .competency_models import EvaluationCycle, CycleCompetency, CycleRater, RATER_TYPES


class RevisionConflict(ValidationError):
    pass


def lock_cycle(cycle, revision, state=None):
    query = EvaluationCycle.objects.filter(pk=cycle.pk, revision=revision)
    if state:
        query = query.filter(state=state)
    if query.update(revision=F('revision') + 1) != 1:
        raise RevisionConflict('O ciclo mudou noutra janela. Reabra a página.')
    cycle.refresh_from_db()


@transaction.atomic
def open_cycle(cycle, revision):
    lock_cycle(cycle, revision, 'draft')
    cycle.full_clean()
    if cycle.end < timezone.localdate():
        raise ValidationError('O prazo do ciclo já terminou.')
    if not cycle.role.active:
        raise ValidationError('A função seleccionada está inactiva.')
    rules = list(cycle.role.rules.select_related('competency').filter(competency__active=True))
    if not rules or any(not r.validated for r in rules):
        raise ValidationError('Defina competências activas com todas as regras validadas pelos RH.')
    for rule in rules:
        rule.full_clean()
    raters = list(cycle.raters.filter(authorized=True).select_related('user'))
    weights = {kind: getattr(cycle, 'weight_' + kind) for kind, _ in RATER_TYPES}
    counts = {kind: sum(r.kind == kind for r in raters) for kind, _ in RATER_TYPES}
    if any(not r.user.is_active for r in raters):
        raise ValidationError('Todos os avaliadores autorizados precisam de uma conta activa.')
    for kind, label in RATER_TYPES:
        if weights[kind] > 0 and not counts[kind]:
            raise ValidationError(f'Autorize pelo menos um avaliador de tipo {label}.')
        if weights[kind] == 0 and counts[kind]:
            raise ValidationError(f'O tipo {label} tem peso zero: retire a autorização ou ajuste os pesos.')
    if counts['self'] > 1:
        raise ValidationError('Só pode existir uma autoavaliação por ciclo.')
    if weights['peer'] > 0 and counts['peer'] < cycle.minimum_peers:
        raise ValidationError(f'Autorize pelo menos {cycle.minimum_peers} colegas distintos.')
    config = {
        'version': cycle.revision, 'weights': {k: str(v) for k,v in weights.items()},
        'minimum_coverage': cycle.minimum_coverage, 'minimum_peers': cycle.minimum_peers,
        'expected_raters': counts, 'role': cycle.role.title,
        'employee_function': cycle.employee.function,
        'rules': [{'competency': r.competency_id, 'validated_by': r.validated_by,
                   'validated_on': r.validated_on.isoformat()} for r in rules],
    }
    CycleCompetency.objects.bulk_create([CycleCompetency(cycle=cycle, competency=r.competency,
        name=r.competency.name, dimension=r.competency.dimension, description=r.competency.description,
        expected=r.expected, weight=r.weight) for r in rules])
    now = timezone.now()
    cycle.raters.filter(authorized=True).update(invited_at=now)
    EvaluationCycle.objects.filter(pk=cycle.pk).update(state='open', config=config, opened_at=now)
    cycle.refresh_from_db()


@transaction.atomic
def finish_cycle(cycle, revision, cancel=False):
    lock_cycle(cycle, revision)
    if cycle.state not in (('draft','open') if cancel else ('open',)):
        raise ValidationError('Esta transição de estado não é permitida.')
    EvaluationCycle.objects.filter(pk=cycle.pk).update(
        state='cancelled' if cancel else 'closed', closed_at=timezone.now())
    cycle.refresh_from_db()


def cycle_results(cycle):
    """Never expose live means: that would permit inference from successive submissions."""
    empty = {'available': False, 'items': [], 'overall': None,
             'reason': 'Resultados disponíveis apenas após o fecho do ciclo.'}
    if cycle.state != 'closed' or not cycle.config:
        return empty
    config = cycle.config
    weights = {k: Decimal(v) for k,v in config['weights'].items()}
    items = list(cycle.items.all())
    raters = list(cycle.raters.filter(authorized=True, submitted_at__isnull=False).prefetch_related('responses'))
    responses = {(r.pk, a.item_id): a.score for r in raters for a in r.responses.all()}
    result = []
    for item in items:
        means = {}
        eligible = True
        for kind, label in RATER_TYPES:
            if weights[kind] == 0:
                continue
            scores = [responses[(r.pk, item.pk)] for r in raters
                      if r.kind == kind and (r.pk, item.pk) in responses]
            expected = config['expected_raters'][kind]
            coverage = Decimal(len(scores)) * 100 / expected if expected else Decimal(0)
            minimum = config['minimum_peers'] if kind == 'peer' else 1
            if len(scores) < minimum or coverage < config['minimum_coverage']:
                eligible = False
            means[kind] = sum(map(Decimal, scores)) / len(scores) if scores else None
        score = sum((means[k] * w / 100 for k,w in weights.items() if w), Decimal(0)) if eligible else None
        # No component means or comments are returned, even when the total is withheld.
        result.append({'id': item.pk, 'name': item.name, 'expected': item.expected, 'weight': item.weight,
            'score': score, 'gap': max(Decimal(0), Decimal(item.expected) - score) if score is not None else None})
    overall = None
    if result and all(r['score'] is not None for r in result):
        overall = sum((r['score'] * r['weight'] for r in result), Decimal(0)) / sum(r['weight'] for r in result)
    return {'available': overall is not None, 'items': result, 'overall': overall,
        'reason': '' if overall is not None else 'Cobertura insuficiente ou mínimo de colegas não atingido; valores não divulgados.'}

"""Coverage against the DRH reference headcount (spec KPI 4, control data C1).

A reference is used only when it matches the selected period exactly and the
same organisational scope (direction, category, gender; empty means "all").
Otherwise coverage keeps the current-registry denominator, labelled as such.
References are never summed or averaged across months or scopes.
"""
from decimal import Decimal, ROUND_HALF_UP

DIMENSIONS = ('direction', 'category', 'gender')


def _value(filters, key):
    value = (filters or {}).get(key)
    return str(value).strip() if value not in (None, '') else ''


def find_reference(scope, references=None):
    if scope is None or not scope.date_from or not scope.date_to:
        return None
    if references is None:
        from .models import WorkforceReference
        references = WorkforceReference.objects.filter(period_start=scope.date_from, period_end=scope.date_to)
    wanted = {key: _value(scope.filters, key) for key in DIMENSIONS}
    for reference in references:
        if (reference.period_start, reference.period_end) != (scope.date_from, scope.date_to):
            continue
        if all((getattr(reference, key) or '').strip() == wanted[key] for key in DIMENSIONS):
            return reference
    return None


def coverage(people, registry_count, scope, references=None):
    """Return the coverage reading with an explicit denominator source."""
    reference = find_reference(scope, references)
    if reference is not None:
        denominator, source = reference.headcount, 'drh_reference'
    else:
        denominator, source = registry_count, 'current_registry'
    value = None
    if denominator:
        value = (Decimal(people) * 100 / denominator).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return {
        'value': value,
        'numerator': people,
        'denominator': denominator,
        'source': source,
        'label': 'Efectivo de referência DRH' if source == 'drh_reference' else 'Cadastro actual disponível',
        'reference': reference,
        # A value above 100% signals a universe mismatch; it is shown, not clamped.
        'status': 'no_data' if value is None else 'inconsistent' if value > 100 else 'valid',
    }

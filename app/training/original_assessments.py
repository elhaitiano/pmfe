"""Original source assessments; never infer academic approval or a /20 grade."""
from decimal import Decimal, InvalidOperation

def percentage(assessment):
    if not assessment or assessment.get('kind') != 'percentage': return None
    try:
        value=Decimal(str(assessment['normalized_ratio']))
        if not value.is_finite() or not 0 <= value <= 1: return None
        return value * 100
    except (KeyError,InvalidOperation,TypeError,ValueError): return None

def label(assessment):
    if not assessment:return ''
    raw=str(assessment.get('raw',''))
    value=percentage(assessment)
    if value is None:return raw
    formatted=format(value,'f').rstrip('0').rstrip('.') if '.' in format(value,'f') else format(value,'f')
    return f"{formatted.replace('.',',')}% (fonte: {raw})"

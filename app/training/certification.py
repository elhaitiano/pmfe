"""Certificate indicators (spec KPI 10 and 11, control data C3/C4).

Read-only. States are read as recorded and never inferred from one another:
- ``certificate_issued`` is the administrative issue state;
- ``certified`` is the delivery to the participant;
- ``certificate_obtained`` is the explicit confirmation that the participant
  obtained the certificate; it is never deduced from attendance or results.

KPI 11 is computed on one identified set: the participations whose certificate
is recorded as issued. A delivery recorded without a confirmed issue is not
counted; it is reported as an inconsistency to regularise. KPI 10 follows the
same rule on the set of obtained certificates. KPI 12 stays inactive until an
institutional classification scale is defined.
"""
from decimal import Decimal, ROUND_HALF_UP


def _rate(numerator, denominator):
    if not denominator:
        return None
    return (Decimal(numerator) * 100 / denominator).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def _within(value, date_from, date_to):
    return bool(value) and (date_from is None or value >= date_from) and (date_to is None or value <= date_to)


def certificate_indicators(rows, date_from=None, date_to=None):
    rows = list(rows)
    issued = [r for r in rows if r.certificate_issued is True]
    delivered_of_issued = [r for r in issued if r.certified is True]
    delivered_any = [r for r in rows if r.certified is True]
    rate = _rate(len(delivered_of_issued), len(issued))
    inconsistent = sum(r.certified is True and r.certificate_issued is not True for r in rows)
    obtained = [r for r in rows if getattr(r, 'certificate_obtained', None) is True]
    issued_of_obtained = [r for r in obtained if r.certificate_issued is True]
    issue_rate = _rate(len(issued_of_obtained), len(obtained))
    issued_without_obtained = sum(getattr(r, 'certificate_obtained', None) is not True for r in issued)
    return {
        'issue_rate': {
            'value': issue_rate,
            'numerator': len(issued_of_obtained),
            'denominator': len(obtained),
            'status': 'no_data' if issue_rate is None else 'partial' if issued_without_obtained else 'valid',
            'excluded_issued_without_obtained': issued_without_obtained,
        },
        'classification': {'value': None, 'status': 'inactive', 'reason': 'institutional_scale_undefined'},
        'delivery_rate': {
            'value': rate,
            'numerator': len(delivered_of_issued),
            'denominator': len(issued),
            'status': 'no_data' if rate is None else 'partial' if inconsistent else 'valid',
            'excluded_delivered_without_issue': inconsistent,
        },
        'obtained': len(obtained),
        'not_obtained': sum(getattr(r, 'certificate_obtained', None) is False for r in rows),
        'obtained_unknown': sum(getattr(r, 'certificate_obtained', None) is None for r in rows),
        'to_issue': sum(r.certificate_issued is False for r in obtained),
        'issue_pending_unknown': sum(r.certificate_issued is None for r in obtained),
        'issued': len(issued),
        'not_issued': sum(r.certificate_issued is False for r in rows),
        'issue_unknown': sum(r.certificate_issued is None for r in rows),
        'delivered': len(delivered_any),
        'delivered_of_issued': len(delivered_of_issued),
        'to_deliver': sum(r.certified is False for r in issued),
        'delivery_unknown': sum(r.certified is None for r in issued),
        'people_delivered': len({r.employee_id for r in delivered_any}),
        # Dated events in the period. Undated events are counted apart, never
        # attributed to a month.
        'issued_in_period': sum(_within(r.certificate_issue_date, date_from, date_to) for r in issued),
        'issued_undated': sum(not r.certificate_issue_date for r in issued),
        'delivered_in_period': sum(_within(r.certificate_delivery_date, date_from, date_to) for r in delivered_any),
        'delivered_undated': sum(not r.certificate_delivery_date for r in delivered_any),
    }

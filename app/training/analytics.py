from .training_areas import AREAS, AREA_COLORS
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
'Shared calculations for the profile, history, forms and printable record.'
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from django.utils import timezone

def attendance_statistics(rows):
    """The shared attendance calculation, without unrelated document indicators."""
    from .operational_kpis import attendance_summary
    summary=attendance_summary(rows)
    measurable=list({row.pk:row for row in rows if row.pk in summary.attendance_pairs}.values())
    metric=summary.metrics['attendance']
    return {'attendance':metric.value,'attendance_days':sum(row.attendance_days for row in measurable),
        'presences':sum(row.attendance for row in measurable),'verified_attendance_records':len(measurable),
        'verified_hours':summary.metrics['hours'].value,'verified_hours_records':len(summary.hours_by_participation),
        'attendance_present_hours':metric.numerator,'attendance_planned_hours':metric.denominator,
        'attendance_status':metric.status,'attendance_exclusions':dict(metric.reasons),
        'attendance_weighting':'hours'}


def statistics(rows):
    rows = consolidated(list(rows))
    attended = [row for row in rows if row.status != 'Planeado']
    attendance = attendance_statistics(rows)
    from .certificate_register import certificate_statistics
    certificate_metrics = certificate_statistics(rows)
    from .operational_kpis import certification_summary
    certification=certification_summary(rows)
    pending_delivery = sum(row.certificate_issued is True and not row.delivery_reported for row in rows)
    original = [row.original_assessment for row in rows if row.original_assessment]
    from .original_assessments import percentage
    percentages = [percentage(x) for x in original if percentage(x) is not None]
    from .data_validation import fact
    grades = [row.presentation_grade for row in rows if row.presentation_grade is not None and fact(row,'evaluation')]
    return {**compatibility_metrics(rows), **certificate_metrics, 'registered_people': len({row.employee_id for row in rows}), 'participations': len(rows), 'certificates_reported': certificate_metrics['certificates_reported'], 'certificates_reported_pending': pending_delivery, 'certificate_emission_unrecorded':sum(row.certificate_issued is None and not row.delivery_reported for row in rows), 'simulated_graded_count': sum((row.simulated_grade is not None for row in rows)), 'certificates_delivery_undeclared': sum((row.certified is None and row.declared_delivery_date is None for row in rows)), 'certificates_delivery_declared': sum((row.declared_delivery_date is not None for row in rows)), 'certificates_receipt_associated': sum((row.delivery_receipt_associated for row in rows)), 'original_assessments_count': len(original), 'original_percentages_count': len(percentages), 'original_classifications_count': len(original) - len(percentages), 'original_percentage_average': sum(percentages) / len(percentages) if percentages else None, 'total': len(rows), 'hours': attendance['verified_hours'] if attendance['verified_hours'] is not None else Decimal(0), 'planned_hours': sum((row.planned_hours for row in rows if row.planned_hours is not None), Decimal(0)), **attendance, 'verified_hours_records':attendance['verified_hours_records'], 'hours_pending_count': sum((row.completed_hours is None for row in attended)), 'attendance_provisional': any((row.status in ('Em Curso', 'Por confirmar') or not row.attendance_confirmed or row.days is None for row in attended)), 'unknown_duration_count': sum((row.days is None or row.hours_per_day is None for row in rows)), 'unconfirmed_attendance_count': sum((not row.attendance_confirmed for row in attended)), 'certification':certification,'certificates_obtained':certification['obtained'],'certificate_obtained_known':certification['obtained']+certification['not_obtained'],'certificates': certificate_metrics['certificates_reported'], 'certificates_issued': certificate_metrics['certificates_issued'], 'certificate_declarations': sum((bool(row.certificate_declaration) for row in rows)), 'certificates_hr': sum((row.certificate_hr_delivered is True for row in rows)), 'certificates_pending': pending_delivery, 'certificates_not_delivered': sum((row.certified is False for row in rows)), 'planned_count': sum((row.status == 'Planeado' for row in rows)), 'completed_count': sum((row.status == 'Concluído' and fact(row,'completion') for row in rows)), 'graded_count': len(grades), 'average': sum(grades) / len(grades) if grades else None, 'unclassified_participations': sum((not r.area for r in rows)), 'unclassified_hours': sum((r.completed_hours or Decimal(0) for r in rows if not r.area), Decimal(0))}

def distribution(rows):
    rows = list(rows)
    grouped = {}
    from .operational_kpis import attendance_summary
    by_id=attendance_summary(rows).hours_by_participation
    total=sum(by_id.values(),Decimal(0))
    if not total:
        return []
    for row in rows:
        if row.area not in AREAS or row.pk not in by_id:
            continue
        item = grouped.setdefault(row.area, {'hours': Decimal(0), 'count': 0})
        item['hours'] += by_id[row.pk]
        item['count'] += 1
    items = []
    accumulated = Decimal(0)
    for label, values in grouped.items():
        percent = values['hours'] * 100 / total
        floor = int(percent.to_integral_value(rounding=ROUND_FLOOR))
        items.append({'label': label, 'hours': float(values['hours']), 'count': values['count'], 'percent': float(percent), 'display_percent': floor, 'fraction': percent - floor, 'remainder': float(100 - percent), 'offset': float(25 - accumulated), 'color': AREA_COLORS[label]})
        accumulated += percent
    target = int(accumulated.to_integral_value(rounding=ROUND_HALF_UP))
    for i in sorted(range(len(items)), key=lambda i: (-items[i]['fraction'], items[i]['label']))[:target - sum((x['display_percent'] for x in items))]:
        items[i]['display_percent'] += 1
    for item in items:
        del item['fraction']
    return items

def format_phone(value):
    value = value or ''
    digits = ''.join((c for c in value if c.isdigit()))
    if len(digits) == 9:
        return ' '.join((digits[i:i + 3] for i in range(0, 9, 3)))
    if len(digits) == 12 and digits.startswith('244'):
        return '+244 ' + ' '.join((digits[i:i + 3] for i in range(3, 12, 3)))
    return value

def years_since(value):
    if not value:
        return None
    today = timezone.localdate()
    return today.year - value.year - ((today.month, today.day) < (value.month, value.day))

def profile_fields(employee):
    source = employee.source_data or {}
    age, seniority = (years_since(employee.birth_date), years_since(employee.admission_date))
    return [('N.º Mecanográfico', employee.number), ('Direcção', employee.direction), ('Função', employee.function), ('Categoria', employee.category), ('Antiguidade', f'{seniority} anos' if seniority is not None else ''), ('N.º BI', employee.identity_number), ('Idade', f'{age} anos' if age is not None else ''), ('Género', employee.gender), ('N.º Telemóvel', format_phone(employee.phone)), ('E-mail', employee.email), ('Centro de Custo', employee.cost_center), ('Área Alocada', employee.area), ('N.º INSS', employee.social_security_number), ('Estado civil', employee.marital_status), ('Empresa', employee.company), ('Data de Nascimento', employee.birth_date), ('Data de Admissão', employee.admission_date), ('Telefone 2', format_phone(source.get('Telefone 2', ''))), ('Origem do registo', employee.registration_origin), ('Estado de revisão', employee.review_status), ('Validação do registo EPAL', employee.registration_validation), ('Linha fonte EPAL', employee.source_row)]

def registration_header_fields(employee):
    """Row-major pairs from the approved EPAL registration sheet."""
    fields = dict(profile_fields(employee))
    return [('Nº Mec.', employee.number), ('Género', employee.gender), ('Categoria', employee.category), ('Nº BI', employee.identity_number), ('Função', employee.function), ('Idade', fields['Idade']), ('Direcção', employee.direction), ('Tel. 1', format_phone(employee.phone)), ('Antiguidade', fields['Antiguidade']), ('Tel. 2', format_phone((employee.source_data or {}).get('Telefone 2', ''))), ('E-mail', employee.email)]
EXCLUDED_KEYS = {'pilar3b:' + p for p in ('PF-00285', 'PF-00434', 'PF-00459', 'PF-01388', 'PF-01854')}

def consolidated(rows):
    from . import data_validation as validation
    if validation.enabled():
        return validation.official(rows)
    return [r for r in rows if getattr(r, 'source_key', None) not in EXCLUDED_KEYS]

def confirmed(row):
    from .data_validation import fact
    return bool(row.attendance_confirmed and row.status != 'Planeado' and fact(row, 'attendance'))

def realised_hours(row):
    return row.completed_hours or Decimal(0) if confirmed(row) else Decimal(0)

def documentary_states(rows):
    from .models import TrainingDocumentState
    missing = [r.pk for r in rows if 'document_state' not in r._state.fields_cache]
    states = {s.training_id: s for s in TrainingDocumentState.objects.filter(training_id__in=missing)} if missing else {}
    for row in rows:
        if 'document_state' not in row._state.fields_cache:
            row._state.fields_cache['document_state'] = states.get(row.pk)

def document_value(row, field):
    if field=='learner_delivered' and row.certified is False and ((row.indicator_source_data or {}).get('_certificate_summary') or {}).get('handover'):
        return False
    from .data_validation import fact
    if not fact(row, field):
        return None
    if getattr(row,'validated_certificates',[]) and field in ('issued','learner_delivered'):
        return True
    state = getattr(row, 'document_state', None)
    value = getattr(state, field, None) if state else None
    if value is not None:return value
    return {'issued':row.certificate_issued,'learner_delivered':row.certified,
            'hr_delivered':row.certificate_hr_delivered}.get(field)


def compatibility_metrics(rows):
    """Document unknowns explicitly for existing historical reports and exports."""
    documentary_states(rows)
    from .source_achievement import attach
    from .evaluations import result
    attach(rows)
    attended=[r for r in rows if confirmed(r) and r.completed_hours is not None]
    for row in rows: row.documented_hours=row.completed_hours if confirmed(row) else None
    issued=[document_value(r,'issued') for r in rows]
    learners=[document_value(r,'learner_delivered') for r in rows]
    hr=[document_value(r,'hr_delivered') for r in rows]
    percentages=[r.source_achievement['percentage'] for r in rows if r.source_achievement and r.source_achievement.get('percentage') is not None]
    evaluations=[result(r) for r in rows]
    numeric=[r['numeric'] for r in evaluations if r['numeric'] is not None]
    return {
        'hours_value':sum((r.completed_hours for r in attended),Decimal(0)) if attended else Decimal(0) if rows and all(r.status=='Planeado' for r in rows) else None,
        'attendance_coverage':len(attended),'attendance_unknown':len(rows)-len(attended),
        'issued_count':issued.count(True),'issued_no':issued.count(False),'issued_known':sum(v is not None for v in issued),'issued_value':issued.count(True) if any(v is not None for v in issued) else None,'issued_pending':issued.count(None),
        'learner_delivery_value':learners.count(True) if any(v is not None for v in learners) else None,'learner_delivery_known':sum(v is not None for v in learners),'learner_delivery_unknown':learners.count(None),
        'hr_delivery_count':hr.count(True),'hr_delivery_value':hr.count(True) if any(v is not None for v in hr) else None,'hr_delivery_pending':hr.count(None),
        'achievement_percent_count':len(percentages),'achievement_percent_average':sum(percentages)/len(percentages) if percentages else None,
        'numeric_evaluations':len(numeric),'numeric_average':sum(numeric)/len(numeric) if numeric else None,
        'converted_evaluations':sum(r['calculated'] for r in evaluations),
        'achievement_approved_count':sum(bool(r.source_achievement and r.source_achievement.get('approved') and result(r)['available']) for r in rows),
        'achievement_qualitative_count':sum(bool(r.source_achievement and r.source_achievement.get('qualitative')) for r in rows),
    }

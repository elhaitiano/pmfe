"""Shared calculations for the profile, history, forms and printable record."""
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from django.utils import timezone

EXCLUDED_KEYS={'pilar3b:'+p for p in ('PF-00285','PF-00434','PF-00459','PF-01388','PF-01854')}

def consolidated(rows):return [r for r in rows if getattr(r,'source_key',None) not in EXCLUDED_KEYS]

def confirmed(row):return bool(row.attendance_confirmed and row.status!='Planeado')

def realised_hours(row):return row.completed_hours if confirmed(row) else Decimal(0)

def documentary_states(rows):
    from .models import TrainingDocumentState
    missing=[r.pk for r in rows if 'document_state' not in r._state.fields_cache]
    states={s.training_id:s for s in TrainingDocumentState.objects.filter(training_id__in=missing)} if missing else {}
    for row in rows:
        if 'document_state' not in row._state.fields_cache:row._state.fields_cache['document_state']=states.get(row.pk)

def document_value(row,field):
    state=getattr(row,'document_state',None)
    value=getattr(state,field,None) if state else None
    return row.certified if field=='learner_delivered' and value is None else value


def statistics(rows):
    rows = consolidated(rows)
    documentary_states(rows)
    from .source_achievement import attach
    attach(rows)
    for row in rows:row.documented_hours=row.completed_hours if confirmed(row) else None
    attended = [row for row in rows if confirmed(row)]
    days = sum(row.days for row in attended)
    presences = sum(row.attendance for row in attended)
    grades = [row.grade for row in rows if row.grade is not None and row.status=='Concluído']
    issued=[document_value(r,'issued') for r in rows]
    learners=[document_value(r,'learner_delivered') for r in rows]
    hr=[document_value(r,'hr_delivered') for r in rows]
    percentages=[r.source_achievement['percentage'] for r in rows if r.source_achievement and r.source_achievement['percentage'] is not None]
    from .evaluations import numeric_values,result
    numeric=numeric_values(rows)
    attendance = (Decimal(presences) * 100 / days).quantize(Decimal('.1'), rounding=ROUND_HALF_UP) if days else None
    return {'total': len(rows), 'hours': sum((realised_hours(row) for row in rows), Decimal(0)),
        'hours_value':sum((realised_hours(row) for row in rows),Decimal(0)) if attended else Decimal(0) if rows and all(r.status=='Planeado' for r in rows) else None,
        'attendance_coverage':len(attended),'attendance_unknown':len(rows)-len(attended),
        'issued_count':issued.count(True),'issued_no':issued.count(False),'issued_known':sum(v is not None for v in issued),'issued_value':issued.count(True) if any(v is not None for v in issued) else None,'issued_pending':issued.count(None),
        'learner_delivery_value':learners.count(True) if any(v is not None for v in learners) else None,'learner_delivery_known':sum(v is not None for v in learners),
        'hr_delivery_count':hr.count(True),'hr_delivery_value':hr.count(True) if any(v is not None for v in hr) else None,'hr_delivery_pending':hr.count(None),
        'achievement_percent_count':len(percentages),'achievement_percent_average':sum(percentages)/len(percentages) if percentages else None,
        'numeric_evaluations':len(numeric),'numeric_average':sum(numeric)/len(numeric) if numeric else None,
        'converted_evaluations':sum(result(r)['calculated'] for r in rows),
        'achievement_approved_count':sum(bool(r.source_achievement and r.source_achievement.get('approved') and result(r)['available']) for r in rows),
        'achievement_qualitative_count':sum(bool(r.source_achievement and r.source_achievement.get('qualitative')) for r in rows),
        'planned_hours': sum((row.planned_hours for row in rows), Decimal(0)),
        'attendance': attendance, 'attendance_days': days, 'presences': presences,
        'attendance_provisional':len(attended)<len([r for r in rows if r.status!='Planeado']) or any(row.status in ('Em Curso', 'Por confirmar') for row in attended),
        'certificates': learners.count(True),
        'certificates_pending': learners.count(None),
        'certificates_not_delivered': learners.count(False),
        'planned_count': sum(row.status == 'Planeado' for row in rows),
        'completed_count': sum(row.status == 'Concluído' for row in rows),
        'graded_count': len(grades),
        'average': sum(grades) / len(grades) if grades else None}


def distribution(rows):
    grouped = {}
    for row in consolidated(rows):
        label = row.area or 'Área por confirmar'
        item = grouped.setdefault(label, {'hours': Decimal(0), 'count': 0})
        item['hours'] += realised_hours(row)
        item['count'] += 1
    total = sum((x['hours'] for x in grouped.values()), Decimal(0))
    if not total:
        return []
    colors = ['#2fb9d6', '#4b82d7', '#41e4c0', '#7aa7ff', '#6b7280']
    items, accumulated = [], Decimal(0)
    for i, (label, values) in enumerate(sorted(grouped.items())):
        percent = values['hours'] * 100 / total
        floor = int(percent.to_integral_value(rounding=ROUND_FLOOR))
        items.append({'label': label, 'hours': float(values['hours']), 'count': values['count'],
            'percent': float(percent), 'display_percent': floor, 'fraction': percent - floor,
            'remainder': float(100 - percent), 'offset': float(25 - accumulated), 'color': colors[i % len(colors)]})
        accumulated += percent
    # Whole-number labels must still add up to 100%.
    for i in sorted(range(len(items)), key=lambda i: (-items[i]['fraction'], items[i]['label']))[:100-sum(x['display_percent'] for x in items)]:
        items[i]['display_percent'] += 1
    for item in items:
        del item['fraction']
    return items


def format_phone(value):
    value = value or ''
    digits = ''.join(c for c in value if c.isdigit())
    if len(digits) == 9:
        return ' '.join(digits[i:i+3] for i in range(0, 9, 3))
    if len(digits) == 12 and digits.startswith('244'):
        return '+244 ' + ' '.join(digits[i:i+3] for i in range(3, 12, 3))
    return value


def years_since(value):
    if not value:
        return None
    today = timezone.localdate()
    return today.year - value.year - ((today.month, today.day) < (value.month, value.day))


def profile_fields(employee):
    source = employee.source_data or {}
    age, seniority = years_since(employee.birth_date), years_since(employee.admission_date)
    return [('N.º Mecanográfico', employee.number), ('Direcção', employee.direction),
        ('Categoria profissional', employee.category), ('Antiguidade', f'{seniority} anos' if seniority is not None else ''),
        ('N.º BI', source.get('Nº do BI', '')), ('Idade', f'{age} anos' if age is not None else ''),
        ('Género', source.get('Sexo', '')), ('N.º Telemóvel', format_phone(employee.phone)),
        ('E-mail', employee.email), ('Tel. 2', format_phone(source.get('Telefone 2', ''))), ('Centro de Custo', employee.area)]

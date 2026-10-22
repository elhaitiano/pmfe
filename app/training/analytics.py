"""Shared calculations for the profile, history, forms and printable record."""
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from django.utils import timezone


def statistics(rows):
    rows = list(rows)
    attended = [row for row in rows if row.status != 'Planeado']
    days = sum(row.days for row in attended)
    presences = sum(row.attendance for row in attended)
    grades = [row.grade for row in rows if row.grade is not None]
    attendance = (Decimal(presences) * 100 / days).quantize(Decimal('.1'), rounding=ROUND_HALF_UP) if days else None
    return {'total': len(rows), 'hours': sum((row.completed_hours for row in rows), Decimal(0)),
        'planned_hours': sum((row.planned_hours for row in rows), Decimal(0)),
        'attendance': attendance, 'attendance_days': days, 'presences': presences,
        'attendance_provisional': any(row.status in ('Em Curso', 'Por confirmar') for row in attended),
        'certificates': sum(row.certified is True for row in rows),
        'certificates_pending': sum(row.certified is None for row in rows),
        'certificates_not_delivered': sum(row.certified is False for row in rows),
        'planned_count': sum(row.status == 'Planeado' for row in rows),
        'completed_count': sum(row.status == 'Concluído' for row in rows),
        'graded_count': len(grades),
        'average': sum(grades) / len(grades) if grades else None}


def distribution(rows):
    grouped = {}
    for row in rows:
        label = row.area or 'Área por confirmar'
        item = grouped.setdefault(label, {'hours': Decimal(0), 'count': 0})
        item['hours'] += row.completed_hours
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
    return [('N.º Mecanográfico', employee.number), ('Direcção', employee.direction), ('Função', employee.function),
        ('Categoria', employee.category), ('Antiguidade', f'{seniority} anos' if seniority is not None else ''),
        ('N.º BI', employee.identity_number), ('Idade', f'{age} anos' if age is not None else ''),
        ('Género', employee.gender), ('N.º Telemóvel', format_phone(employee.phone)),
        ('E-mail', employee.email), ('Centro de Custo', employee.cost_center),
        ('Área Alocada', employee.area), ('N.º INSS', employee.social_security_number),
        ('Estado civil', employee.marital_status), ('Empresa', employee.company),
        ('Data de Nascimento', employee.birth_date), ('Data de Admissão', employee.admission_date),
        ('Telefone 2', format_phone(source.get('Telefone 2', ''))),
        ('Origem do cadastro', employee.registration_origin), ('Estado de revisão', employee.review_status),
        ('Validação Cadastro EPAL', employee.registration_validation), ('Linha fonte EPAL', employee.source_row)]

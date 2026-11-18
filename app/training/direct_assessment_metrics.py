from decimal import Decimal


def normalise(value, maximum):
    return None if value is None else Decimal(value) * 20 / Decimal(maximum)


def mention(value):
    if value is None:
        return 'Sem avaliação'
    for ceiling, label in [(10, 'Insuficiente'), (14, 'Suficiente'), (16, 'Bom'), (18, 'Muito Bom')]:
        if value < ceiling:
            return label
    return 'Excelente'


def average(values):
    return sum(values, Decimal(0)) / len(values) if values else None


def metrics(rows):
    pairs = [r for r in rows if r['initial'] is not None and r['final'] is not None]
    return dict(total=len(rows), pairs=len(pairs), initial=average([r['initial'] for r in pairs]),
        paired_final=average([r['final'] for r in pairs]),
        evolution=average([r['final'] - r['initial'] for r in pairs]),
        final=average([r['final'] for r in rows if r['final'] is not None]),
        final_count=sum(r['final'] is not None for r in rows),
        satisfaction=average([Decimal(r['satisfaction']) for r in rows if r['satisfaction'] is not None]),
        responses=sum(r['satisfaction'] is not None for r in rows))

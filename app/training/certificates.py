"""A certification is identified by its participation, never by a person's name."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.utils import timezone

FIELDS = ('certificate_obtained','certificate_obtained_date','certificate_issued','certificate_issue_date',
          'certified','certificate_delivery_date','certificate_hr_delivered','certificate_hr_delivery_date',
          'certificate_reference','certificate_actor','certificate_evidence',
          'certificate_declaration','certificate_declared_by','certificate_declaration_source','certificate_declaration_date')
STATES = [('obtained','certificate_obtained','certificate_obtained_date'),
          ('issued','certificate_issued','certificate_issue_date'),
          ('delivered','certified','certificate_delivery_date')]

def snapshot(row):
    return {key:(getattr(row,key).isoformat() if isinstance(getattr(row,key),date) else getattr(row,key)) for key in FIELDS} if row else {}

def validate_transition(row):
    if not row.certificate_actor.strip() or not row.certificate_evidence.strip():
        raise ValidationError('A alteração de certificação exige responsável e referência/observação; anexo não é obrigatório.')
    for label,flag,dated in STATES+[('RH','certificate_hr_delivered','certificate_hr_delivery_date')]:
        value=getattr(row,flag); day=getattr(row,dated)
        if value is True and not day:
            raise ValidationError(f'Indique a data efectiva: {label}.')
        if day and (value is not True or day > timezone.localdate()):
            raise ValidationError(f'Data incompatível com o estado: {label}.')
    if row.certificate_issued is True and row.certificate_obtained is not True:
        raise ValidationError('A emissão exige obtenção explicitamente confirmada; pode confirmar ambas nesta operação.')
    if (row.certified is True or row.certificate_hr_delivered is True) and row.certificate_issued is not True:
        raise ValidationError('A entrega exige emissão explicitamente confirmada.')
    if row.certificate_obtained_date and row.certificate_issue_date and row.certificate_issue_date<row.certificate_obtained_date:
        raise ValidationError('A emissão não pode anteceder a obtenção.')
    for field in ('certificate_delivery_date','certificate_hr_delivery_date'):
        if getattr(row,field) and row.certificate_issue_date and getattr(row,field)<row.certificate_issue_date:
            raise ValidationError('A entrega não pode anteceder a emissão.')

def summary(rows, reference_date=None, start=None):
    reference_date=reference_date or timezone.localdate()
    counts={k:0 for k in ('obtained','issued','delivered','not_issued','not_delivered','issued_unknown','delivered_unknown','obtained_unknown','inconsistent','excluded','issue_events','delivery_events')}
    people=set();cohort=0
    for row in {r.pk:r for r in rows}.values():
        source=snapshot(row)
        recorded_negative=False
        if reference_date<timezone.localdate() and any(source.get(flag) is False for label,flag,dated in STATES):
            last=row.certificate_events.last()
            recorded_negative=bool(last and timezone.localtime(last.recorded_at).date()<=reference_date)
        flags={}
        dates={}
        for label,flag,dated in STATES:
            day=date.fromisoformat(source[dated]) if source.get(dated) else None
            dates[label]=day
            value=source.get(flag)
            # Undated legacy positives remain unknown; current negatives cannot be projected into a past close.
            flags[label]=(day<=reference_date if day else None) if value is True else value if reference_date>=timezone.localdate() or recorded_negative else None
        for label,key in [('issued','issue_events'),('delivered','delivery_events')]:
            day=dates[label]
            if flags[label] is True and day and (not start or day>=start):counts[key]+=1
        if flags['obtained'] is None:counts['obtained_unknown']+=1
        undocumented=any(source.get(flag) is True and not dates[label] for label,flag,dated in STATES)
        inconsistent=(undocumented or flags['issued'] is True and flags['obtained'] is not True or
            flags['delivered'] is True and flags['issued'] is not True or
            dates['obtained'] and dates['issued'] and dates['issued']<dates['obtained'] or
            dates['issued'] and dates['delivered'] and dates['delivered']<dates['issued'])
        if inconsistent:
            counts['inconsistent']+=1;counts['excluded']+=1
            continue
        # Rates use the obtained cohort, not the independent issue/delivery event counts.
        if flags['obtained'] is not True or (start and (not dates['obtained'] or dates['obtained']<start)):
            counts['excluded']+=1
            continue
        cohort+=1;counts['obtained']+=1
        if flags['issued'] is True:counts['issued']+=1
        elif flags['issued'] is False:counts['not_issued']+=1
        else:counts['issued_unknown']+=1
        if flags['issued'] is True:
            if flags['delivered'] is True:
                counts['delivered']+=1;people.add(row.employee_id)
            elif flags['delivered'] is False:counts['not_delivered']+=1
            else:counts['delivered_unknown']+=1
    rate=lambda n,d:(Decimal(n)*100/d).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if d else None
    return {**counts,'issue_rate':rate(counts['issued'],counts['obtained']),
        'delivery_rate':rate(counts['delivered'],counts['issued']),'status':'Parcial' if counts['excluded'] or counts['issued_unknown'] or counts['delivered_unknown'] else 'Confirmado','delivery_people':len(people),
        'reference_date':reference_date,'cohort_start':start,'classification':'Escala institucional não definida; indicador inactivo'}

def export_rows(data):
    return [['Certificação — referência',str(data['reference_date'])],
        ['Coorte de obtenção — início',str(data['cohort_start'] or 'Todos')],
        ['Obtidos',data['obtained']],['Emitidos',data['issued']],['Entregues',data['delivered']],
        ['Emissão (%)',data['issue_rate'],'Numerador',data['issued'],'Denominador',data['obtained']],
        ['Entrega (%)',data['delivery_rate'],'Numerador',data['delivered'],'Denominador',data['issued']],
        ['Por emitir (emissão explicitamente não realizada)',data['not_issued'],'Emissão desconhecida',data['issued_unknown']],
        ['Por entregar (não entregue explicitamente)',data['not_delivered'],'Entrega desconhecida',data['delivered_unknown']],
        ['Certificações excluídas',data['excluded'],'Inconsistentes',data['inconsistent']],
        ['Eventos de emissão no período',data['issue_events']],['Eventos de entrega no período',data['delivery_events']],
        ['Colaboradores distintos com entrega na coorte',data['delivery_people']],['Classificação',data['classification']]]

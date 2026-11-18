from decimal import Decimal
from django.utils.formats import number_format


def documented_grade_label(training):
    facts=(training.source_data or {}).get('_EPAL_CONSOLIDATED_FACTS',{})
    grades=[g for g in facts.get('grades',[]) if g['stage']=='final'] or [g for g in facts.get('grades',[]) if g['stage']=='certificate_mention']
    if len(grades)>1:return 'Valores divergentes nas fontes'
    if grades:
        g=grades[0];suffix=' (menção do certificado)' if g['stage']=='certificate_mention' else ''
        return f"{g['value']}/{g['scale']}"+suffix
    if training.original_assessment.get('raw'):return training.original_assessment['raw']
    return number_format(training.grade,decimal_pos=1)+'/20' if training.grade is not None else 'Sem nota registada'


def documented_grade_percent(training):
    facts=(training.source_data or {}).get('_EPAL_CONSOLIDATED_FACTS',{})
    grades=[g for g in facts.get('grades',[]) if g['stage']=='final'] or [g for g in facts.get('grades',[]) if g['stage']=='certificate_mention']
    if len(grades)>1:return None
    if grades:return float(Decimal(grades[0]['value'])*100/Decimal(grades[0]['scale']))
    if training.grade is not None:return float(training.grade)*5
    from .original_assessments import percentage
    value=percentage(training.original_assessment)
    return float(value) if value is not None else None


def source_chart(rows,selected=''):
    from .ux_presentation import display_rows, grade
    values=[]
    ordered=rows.order_by('start','end','pk') if hasattr(rows,'order_by') else sorted(rows,key=lambda t:(t.start,t.end,t.pk))
    for t in display_rows(ordered):
        result=grade(t)
        if result is not None:values.append((t,result[0],result[1]))
    scales=sorted({scale for _,_,scale in values})
    scale=next((s for s in scales if str(s)==selected),Decimal(100) if Decimal(100) in scales else scales[0] if scales else Decimal(20))
    entries=[(t,g) for t,g,s in values if s==scale];dots=[]
    for i,(t,grade) in enumerate(entries):
        dots.append({'x':round(25+i*260/(len(entries)-1)) if len(entries)>1 else 155,'y':round(130-float(grade/scale)*120),'title':t.ux_title+' · '+t.start.strftime('%d/%m/%Y'),
            'grade':grade,'date_label':f'{t.start:%d/%m/%Y} — {t.end:%d/%m/%Y}','trainer':t.trainer,'simulated':False})
    coordinates=' '.join(f"{p['x']},{p['y']}" for p in dots)
    return {'chart_scale':format(scale,'f'),'chart_mid':scale/2,'chart_scales':[format(s,'f') for s in scales],
        'chart_dots':dots,'chart_points':coordinates,'chart_area':'25,130 '+coordinates+' 285,130',
        'chart_first_date':entries[0][0].start.strftime('%d/%m/%Y') if entries else '', 'chart_last_date':entries[-1][0].start.strftime('%d/%m/%Y') if entries else ''}

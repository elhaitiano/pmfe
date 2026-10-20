"""Pure presentation of original results and explicitly authorised equivalents."""
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP

def final_percentage_source(source):
    if source.get('corrected'):
        return source.get('scope')=='final_achievement'
    columns={'Base de dados':'AE','Base de dados Ago2026':'AD','Agosto - 2026':'Q','Setembro - 2026':'Q'}
    return source.get('source_sheet')=='PARTICIPACOES' and any(
        '%' in cell.get('format','') and not cell.get('formula') and cell.get('type')!='f'
        and columns.get(cell.get('ref','').rsplit(':',1)[0])==cell.get('cell','').rstrip('0123456789')
        for cell in source.get('evidence_cells',[]))

def percentage_to_grade(value):
    try:value=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError):return None
    if not value.is_finite() or not 0<=value<=100:return None
    return (value*Decimal(20)/Decimal(100)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)

def result(training):
    source=getattr(training,'source_achievement',None)
    base={'numeric':None,'original':None,'percentage':None,'qualitative':None,'available':False,'calculated':False,'kind':'unavailable','label':'Sem nota registada'}
    if training.grade is not None:
        return {**base,'numeric':training.grade,'original':str(training.grade),'available':True,'kind':'corrected' if source and source.get('corrected') else 'original','label':f'{training.grade:g}/20'}
    if not source or source.get('unit')=='none':return base
    if source.get('qualitative'):
        return {**base,'original':source['raw'],'qualitative':source['raw'],'available':True,'kind':'qualitative','label':source['raw']}
    percentage=source.get('percentage')
    if percentage is None:
        raw=source.get('raw')
        return {**base,'original':raw,'kind':'unverified','label':f'{raw} (escala por confirmar)'} if raw not in (None,'') else base
    # Only the approved achievement column, not attendance/logistics/satisfaction.
    final_source=final_percentage_source(source)
    grade=percentage_to_grade(percentage) if source.get('approved') and final_source and training.status=='Concluído' else None
    return {**base,'original':source.get('raw',str(percentage)),'percentage':percentage,'numeric':grade,'available':True,'calculated':grade is not None,'kind':'calculated' if grade is not None else 'percentage','label':f'{percentage:g}%'+(f' · {grade:g}/20 (calculada)' if grade is not None else '')}

def numeric_values(rows):
    return [result(t)['numeric'] for t in rows if t.status=='Concluído' and result(t)['numeric'] is not None]

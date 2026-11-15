"""The twelve institutional indicators, shared by executive screens and exports."""
from .kpi_reporting import STATUS, LABELS

GROUPS=['Horas e Formação','Participação e Cobertura','Aprendizagem e Conclusão','Certificação','Qualidade e Avaliação']

def catalogue(stats):
    summary=stats['kpi_summary'];academic=stats['outcomes'];cert=stats['certification'];items=[]
    def add(number,label,value,unit,group,status,numerator=None,denominator=None,note=''):
        items.append(dict(number=number,integral=number in (2,3),label=label,value=value,unit=unit,group=group,status=status,numerator=numerator,denominator=denominator,note=note))
    for number,key,label,group in [(1,'hours','Horas frequentadas',0),(2,'participations','Participações efectivas',1),(3,'people','Colaboradores com participação confirmada',1),(4,'coverage','Taxa de cobertura',1),(5,'hours_per_person','Horas por colaborador participante',0),(6,'attendance','Assiduidade',0)]:
        m=summary.metrics[key]
        note=f'{m.excluded} participações excluídas. '+('Referência: '+('cadastro actual (não histórico)' if summary.scope.reference=='current_registry' else summary.scope.reference) if key=='coverage' else '')
        add(number,label,m.value,LABELS[key][1],GROUPS[group],STATUS[m.status],m.numerator,m.denominator,note)
    add(7,'Avaliação logística',academic['logistics'],'/5',GROUPS[4],'Parcial' if academic['logistics_excluded_groups'] else 'Confirmado' if academic['logistics_count'] else 'Sem dados',denominator=academic['logistics_count'],note=f"{academic['logistics_count']} respostas; equivalente {academic['logistics_percent'] if academic['logistics_percent'] is not None else 'Sem dados'}%. {academic['logistics_excluded_groups']} agregados excluídos.")
    for number,label,value,n,d,pending in [(8,'Taxa de conclusão',academic['completion_rate'],academic['completion_count'],academic['completion_determined'],academic['completion_pending']),(9,'Taxa de aproveitamento',academic['approval_rate'],academic['approved'],academic['result_count'],academic['result_pending'])]:
        add(number,label,value,'%',GROUPS[2],'Parcial' if pending else 'Confirmado' if d else 'Sem dados',n,d,f"{pending} pendentes em {academic['ended_count']} participações elegíveis. Referência: {academic['reference_date']}.")
    for number,label,key,n,d in [(10,'Taxa de emissão de certificados','issue_rate','issued','obtained'),(11,'Taxa de entrega de certificados','delivery_rate','delivered','issued')]:
        add(number,label,cert[key],'%',GROUPS[3],cert['status'] if cert[key] is not None else 'Sem dados',cert[n],cert[d],f"Mesma coorte de obtenção; referência {cert['reference_date']}. {cert['excluded']} excluídos; {cert['inconsistent']} inconsistentes.")
    add(12,'Distribuição por classificação',None,'%',GROUPS[4],'Inactivo',note=cert['classification'])
    return sorted(items,key=lambda i:i['number'])

def panels(items):
    return [dict(title=group,items=[i for i in items if i['group']==group]) for group in GROUPS]

def main_cards(stats):
    m=stats['kpi_summary'].metrics;a=stats['outcomes'];summary=stats['kpi_summary']
    value=lambda v:str(v) if v is not None else 'Sem dados'
    specs=[('Colaboradores com participação confirmada',m['people'].value,'',False,f"Cobertura: {value(m['coverage'].value)}{'%' if m['coverage'].value is not None else ''}",'groups','people'),
        ('Horas frequentadas',m['hours'].value,'h',True,f"H/D: {value(stats['equivalent_days'])} · Dias efectivos: {summary.effective_days if summary.effective_days is not None else 'Por verificar'}",'schedule','hours'),
        ('Assiduidade',m['attendance'].value,'%',True,'Horas verificadas ÷ horas previstas correspondentes','how_to_reg','attendance'),
        ('Aproveitamento',a['approval_rate'],'%',True,f"{a['approved']} aprovadas / {a['result_count']} resultados determinados · {a['result_pending']} pendentes",'verified',None)]
    return [dict(label=label,value=v,suffix=unit,decimal=decimal,note=note,icon=icon,color=color,evidence_status=STATUS[m[key].status] if key else 'Parcial' if a['result_pending'] else 'Confirmado' if a['result_count'] else 'Sem dados') for (label,v,unit,decimal,note,icon,key),color in zip(specs,['#35509a','#1ea0ca','#2e6eae','#164777'])]

def export_rows(stats):
    rows=[['Matriz institucional — 12 KPI'],['N.º','Indicador','Valor','Unidade','Estado','Numerador','Denominador','Âmbito e exclusões']]
    rows += [[i['number'],i['label'],i['value'] if i['value'] is not None else 'Sem dados',i['unit'],i['status'],i['numerator'],i['denominator'],i['note']] for i in catalogue(stats)]
    return rows

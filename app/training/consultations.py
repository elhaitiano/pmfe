"""Shared read-only selection for HTML, Excel and PDF. No silent data filling."""
from collections import Counter,defaultdict
from decimal import Decimal
from django import forms
from .models import Training,Employee,Course
from .analytics import statistics,EXCLUDED_KEYS,document_value
from .source_achievement import attach
from .evaluations import result
from . import historical

MODES=[('all','Todas as participações'),('evaluated','Formandos com avaliações'),('missing','Formandos sem avaliações'),('completed','Formações realizadas'),('groups','Grupos e participantes'),('attendance','Assiduidade e horas'),('issued','Certificados emitidos'),('learner','Certificados recebidos pelos formandos'),('hr','Exemplares entregues aos RH')]
class Filters(forms.Form):
    mode=forms.ChoiceField(label='Consulta',choices=MODES,required=False)
    q=forms.CharField(label='Nome, mecanográfico ou formação',required=False,max_length=200)
    formation=forms.CharField(label='Designação da formação',required=False,max_length=200)
    year=forms.ChoiceField(label='Ano de início',required=False)
    direction=forms.ChoiceField(label='Direcção',required=False)
    action=forms.ChoiceField(label='Acção do catálogo',required=False)
    edition=forms.ChoiceField(label='Grupo',required=False)
    age_band=forms.ChoiceField(label='Faixa etária no início',required=False,choices=[('','Todas'),*historical.AGE_BANDS])
    date_from=forms.DateField(label='Desde',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    date_to=forms.DateField(label='Até',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    def __init__(self,*args,**kw):
        super().__init__(*args,**kw)
        self.fields['year'].choices=[('','Todos')]+[(str(d.year),str(d.year)) for d in Training.objects.dates('start','year',order='DESC')]
        self.fields['direction'].choices=[('','Todas')]+[(x,x) for x in Employee.objects.exclude(direction='').values_list('direction',flat=True).distinct().order_by('direction')]
        self.fields['action'].choices=[('','Todas')]+list(Course.objects.exclude(source_action_id='').values_list('source_action_id','title'))
        labels=historical.presentation_labels()
        self.fields['edition'].choices=[('','Todos')]+[(g['id'],labels[g['id']]+' — '+g['title']+' · '+g['start']) for g in historical.manifest()['editions'].values()]
    def clean(self):
        d=super().clean()
        if d.get('date_from') and d.get('date_to') and d['date_from']>d['date_to']:self.add_error('date_to','A data final deve ser posterior à inicial.')
        return d
def select(data):
    # Physical duplicate rows stay in detail; consolidated indicators exclude them.
    rows=list(Training.objects.select_related('employee','document_state').order_by('-start','employee__number','pk'))
    attach(rows);bindings={r['training'].pk:r for r in historical.records()};labels=historical.presentation_labels()
    selected=[]
    for t in rows:
        r=bindings.get(t.pk);b=r['binding'] if r else None
        t.consultation_group={**r['edition'],'presentation_label':labels[r['edition']['id']]} if b else None
        t.consultation_duplicate=t.source_key in EXCLUDED_KEYS
        t.consultation_level=r['level'] if r else 'unmatched'
        t.consultation_age=historical.age_band(t.employee,t.start)
        t.consultation_issued=document_value(t,'issued');t.consultation_learner=document_value(t,'learner_delivered');t.consultation_hr=document_value(t,'hr_delivered')
        t.evaluation=result(t)
        q=data.get('q','').strip().casefold()
        if q and not any(q in str(v).casefold() for v in (t.employee.name,t.employee.number,t.title)):continue
        if data.get('formation') and data['formation'].casefold() not in t.title.casefold():continue
        if data.get('year') and str(t.start.year)!=data['year']:continue
        if data.get('direction') and t.employee.direction!=data['direction']:continue
        if data.get('action') and (not b or b['action_id']!=data['action']):continue
        if data.get('edition') and (not b or b['edition']!=data['edition']):continue
        if data.get('age_band') and t.consultation_age!=data['age_band']:continue
        if data.get('date_from') and t.end<data['date_from']:continue
        if data.get('date_to') and t.start>data['date_to']:continue
        mode=data.get('mode')
        if mode=='evaluated' and not t.evaluation['available']:continue
        if mode=='completed' and t.status!='Concluído':continue
        if mode=='groups' and not b:continue
        if mode=='issued' and t.consultation_issued is not True:continue
        if mode=='learner' and t.consultation_learner is not True:continue
        if mode=='hr' and t.consultation_hr is not True:continue
        selected.append(t)
    if data.get('mode')=='missing':
        evaluated_people={t.employee_id for t in selected if t.evaluation['available']}
        selected=[t for t in selected if t.employee_id not in evaluated_people]
    return selected
def summary(rows):
    consolidated=[t for t in rows if not t.consultation_duplicate]
    groups=defaultdict(list);directions=defaultdict(list);formations=defaultdict(list);years=defaultdict(list);ages=defaultdict(list)
    for t in consolidated:
        if t.consultation_group:groups[t.consultation_group['id']].append(t)
        directions[t.employee.direction or 'Direcção por confirmar'].append(t)
        formations[t.title].append(t);years[str(t.start.year)].append(t);ages[dict(historical.AGE_BANDS)[t.consultation_age]].append(t)
    def entry(label,items):
        values=[t.evaluation['numeric'] for t in items if t.evaluation['numeric'] is not None]
        return {'label':label,'participations':len(items),'people':len({t.employee_id for t in items}),'numeric_count':len(values),'average':sum(values)/len(values) if values else None,'qualitative_count':sum(t.evaluation['qualitative'] is not None for t in items)}
    return {'physical':len(rows),'participations':len(consolidated),'duplicates':len(rows)-len(consolidated),'people':len({t.employee_id for t in consolidated}),'numeric_people':len({t.employee_id for t in consolidated if t.evaluation['numeric'] is not None}),'qualitative_people':len({t.employee_id for t in consolidated if t.evaluation['qualitative'] is not None}),'stats':statistics(consolidated),'groups':[entry(items[0].consultation_group['presentation_label']+' — '+items[0].consultation_group['title'],items) for items in groups.values()],'directions':[entry(label,items) for label,items in sorted(directions.items())],'formations':[entry(label,items) for label,items in sorted(formations.items())],'years':[entry(label,items) for label,items in sorted(years.items())],'ages':[entry(label,items) for label,items in sorted(ages.items())]}
HEADERS=['Mecanográfico','Formando','Direcção','Formação','Grupo de consulta','Início','Fim','Horas comprovadas (H)','Assiduidade (%)','Resultado original','Nota /20','Tipo da nota','Certificado emitido','Recebido pelo formando','Entregue aos RH','Registo duplicado']
def state(v):return 'Sim' if v is True else 'Não' if v is False else 'Por confirmar'
def export_row(t):
    return [t.employee.number,t.employee.name,t.employee.direction,t.title,t.consultation_group['presentation_label'] if t.consultation_group else 'Por verificar',t.start.isoformat(),t.end.isoformat(),t.completed_hours if t.attendance_confirmed else None,t.attendance_percent if t.attendance_confirmed else None,t.evaluation['original'],t.evaluation['numeric'],'Calculada' if t.evaluation['calculated'] else 'Original' if t.evaluation['available'] else 'Sem nota registada',state(t.consultation_issued),state(t.consultation_learner),state(t.consultation_hr),'Sim — excluído dos totais' if t.consultation_duplicate else 'Não']

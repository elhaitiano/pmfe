"""Period selection, provenance and drill-downs for the executive centre."""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode
from django import forms
from django.urls import reverse
from django.utils import timezone
from .dashboard import DashboardFilters, executive_data
from .analytics import statistics
from .models import Employee, Training

MONTHS = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
COLORS = ['#41e4c0','#5d8eea','#fbbf24','#38bdf8','#a78bfa','#fb7185','#2dd4bf','#c084fc']

class ExecutiveFilters(DashboardFilters):
    period = forms.ChoiceField(label='Periodicidade', required=False, choices=[('', 'Visão global'), ('month','Mensal'),('quarter','Trimestral'),('semester','Semestral'),('year','Anual'),('custom','Personalizado')])
    month = forms.TypedChoiceField(label='Mês', required=False, coerce=int, choices=[('', 'Seleccionar')] + [(i+1, x) for i,x in enumerate(MONTHS)])
    quarter = forms.TypedChoiceField(label='Trimestre', required=False, coerce=int, choices=[('', 'Seleccionar')] + [(i, f'{i}º trimestre') for i in range(1,5)])
    semester = forms.TypedChoiceField(label='Semestre', required=False, coerce=int, choices=[('', 'Seleccionar'),(1,'1º semestre'),(2,'2º semestre')])
    date_from = forms.DateField(label='De', required=False, widget=forms.DateInput(attrs={'type':'date'}))
    date_to = forms.DateField(label='Até', required=False, widget=forms.DateInput(attrs={'type':'date'}))

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        years = {timezone.localdate().year} | {int(v) for v,_ in self.fields['year'].choices if v}
        years |= {d.year for d in Training.objects.dates('certificate_delivery_date','year')}
        self.fields['year'].choices = [('', 'Todos os anos')] + [(str(y),str(y)) for y in sorted(years,reverse=True)]
        self.fields['year'].label='Ano'
        self.order_fields(['period','year','month','quarter','semester','date_from','date_to','direction','area','status'])

    def clean(self):
        data=super().clean();period=data.get('period','')
        if period and period!='custom' and not data.get('year'):
            self.add_error('year','Seleccione o ano do relatório.')
        required={'month':'month','quarter':'quarter','semester':'semester'}.get(period)
        if required and not data.get(required):self.add_error(required,'Seleccione este campo.')
        if period=='custom':
            for f in ('date_from','date_to'):
                if not data.get(f):self.add_error(f,'Indique a data.')
            if data.get('date_from') and data.get('date_to') and data['date_to']<data['date_from']:
                self.add_error('date_to','A data final não pode anteceder a inicial.')
        return data


def bounds(data):
    period=data.get('period','');year=int(data['year']) if data.get('year') else None
    if period=='custom':return data['date_from'],data['date_to'],f"{data['date_from']:%d/%m/%Y} a {data['date_to']:%d/%m/%Y}"
    if not year:return None,None,'Visão global - todos os períodos'
    start_month,end_month=1,12
    if period=='month':start_month=end_month=data['month']
    elif period=='quarter':start_month=(data['quarter']-1)*3+1;end_month=start_month+2
    elif period=='semester':start_month=(data['semester']-1)*6+1;end_month=start_month+5
    label=f'Anual {year}'
    if period=='month':label=f'{MONTHS[start_month-1]} {year}'
    elif period=='quarter':label=f"{data['quarter']}º trimestre de {year}"
    elif period=='semester':label=f"{data['semester']}º semestre de {year}"
    return date(year,start_month,1),date(year,end_month,calendar.monthrange(year,end_month)[1]),label


def previous_bounds(start,end,period):
    if not start:return None,None
    months={'month':1,'quarter':3,'semester':6,'year':12,'':12}.get(period)
    if months:
        ordinal=start.year*12+start.month-1-months;y,m=divmod(ordinal,12)
        return date(y,m+1,1),start-timedelta(days=1)
    length=(end-start).days+1
    return start-timedelta(days=length),start-timedelta(days=1)


def base_query(data):
    rows=Training.objects.select_related('employee').all();employees=Employee.objects.all()
    if data.get('direction'):rows=rows.filter(employee__direction=data['direction']);employees=employees.filter(direction=data['direction'])
    for key in ('area','status'):
        if data.get(key):rows=rows.filter(**{key:data[key]})
    return rows,employees


def selection(request):
    form=ExecutiveFilters(request.GET);valid=form.is_valid();selected=form.cleaned_data if valid else {}
    start,end,label=bounds(selected) if valid else (None,None,'Filtros inválidos')
    base,employees=base_query(selected)
    rows=base.filter(start__range=(start,end)) if start else base
    if not valid:rows=rows.none();base=base.none()
    return form,selected,start,end,label,list(rows),list(employees),base,valid


def slice_rows(rows,kind,value):
    if kind=='area':return [r for r in rows if (r.area in ('','Área por confirmar') if value=='__unconfirmed__' else r.area==value)]
    if kind=='direction':return [r for r in rows if (r.employee.direction in ('','Direcção por confirmar') if value=='__unconfirmed__' else r.employee.direction==value)]
    if kind=='status':return [r for r in rows if r.status==value]
    if kind=='result':return [r for r in rows if r.status=='Concluído' and r.result==value]
    if kind=='time':return [r for r in rows if str(r.start.year)==value or r.start.strftime('%Y-%m')==value or r.start.isoformat()==value]
    if kind=='person':return [r for r in rows if str(r.employee_id)==value]
    if kind=='certificate':return [r for r in rows if (r.certified is True if value=='yes' else r.certified is False if value=='no' else r.certified is None)]
    return rows


def analyse(rows,employees,selected,start,end,label,base=None):
    year=str(start.year) if start and end and start.year==end.year else ''
    data=executive_data(rows,employees,year)
    data.update({'rows':rows,'employees':employees,'period_label':label,'date_from':start,'date_to':end,'selected':selected,'generated_at':timezone.localtime()})
    query={k:(v.isoformat() if isinstance(v,date) else v) for k,v in selected.items() if v not in ('',None)}
    def drill(kind,value='',title=''):
        return reverse('executive_explore')+'?'+urlencode({**query,'slice':kind,'value':value,'label':title})
    data['query']=urlencode(query)
    data['area_donut']=[];total=data['stats']['hours'];offset=Decimal(0)
    for i,bar in enumerate(data['area_bars']):
        real_label='__unconfirmed__' if bar['label']=='Área por confirmar' and any(not r.area for r in rows) else bar['label']
        pct=bar['value']*100/total if total else Decimal(0)
        bar.update(color=COLORS[i%len(COLORS)],href=drill('area',real_label,bar['label']),percent=float(pct))
        data['area_donut'].append({**bar,'offset':float(-offset),'remainder':float(100-pct)})
        offset+=pct
    for bar in data['status_bars']:bar['href']=drill('status',bar['label'],bar['label'])
    if start and end and year and selected.get('period') in ('quarter','semester','custom'):
        data['timeline_bars']=data['timeline_bars'][start.month-1:end.month]
    if selected.get('period')=='month':
        daily={}
        for row in rows:daily[row.start.isoformat()]=daily.get(row.start.isoformat(),Decimal(0))+row.completed_hours
        peak=max(daily.values(),default=0)
        data['timeline_bars']=[{'label':date.fromisoformat(k).strftime('%d/%m'),'value':v,'width':float(v/peak*100) if peak else 0,'href':drill('time',k,k)} for k,v in sorted(daily.items())]
        data['timeline_title']='Horas por dia de início (dias com registos)'
    for bar in data['timeline_bars']:
        if 'href' in bar:continue
        if year:
            n=['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'].index(bar['label'])+1
            key=f'{year}-{n:02d}'
        else:key=bar['label']
        bar['href']=drill('time',key,bar['label'])
    for item in data['results']:item['href']=drill('result',item['label'],item['label'])
    direction_grades={};people={}
    for row in rows:
        people.setdefault(row.employee_id,{'employee':row.employee,'rows':[]})['rows'].append(row)
    talents=[]
    for person in people.values():
        stats=statistics(person['rows']);grades=[r.grade for r in person['rows'] if r.status=='Concluído' and r.grade is not None]
        average=sum(grades)/len(grades) if grades else None
        person.update(stats=stats,average=average,graded=len(grades),href=drill('person',person['employee'].pk,person['employee'].name))
        if average is not None:talents.append(person)
    talents.sort(key=lambda p:(-p['average'],-p['graded'],p['employee'].name))
    data['talents']=talents[:10]
    data['scatter']=[{'name':p['employee'].name,'x':55+float(p['stats']['attendance'])*4.8,'y':220-float(p['average'])*9,'radius':min(14,4+len(p['rows'])),'href':p['href'],'average':p['average'],'attendance':p['stats']['attendance']} for p in talents if p['stats']['attendance'] is not None]
    for d in data['directions_table']:
        group=[r for r in rows if (r.employee.direction or 'Direcção por confirmar')==d['label']]
        ds=statistics(group);grades=[r.grade for r in group if r.status=='Concluído' and r.grade is not None]
        d.update(attendance=ds['attendance'],average=sum(grades)/len(grades) if grades else None,href=drill('direction','__unconfirmed__' if d['label']=='Direcção por confirmar' else d['label'],d['label']))
    data['directions_active']=len({r.employee.direction for r in rows if r.employee.direction})
    data['locations']=len({r.location for r in rows if r.location})
    data['trainers']=len({r.trainer for r in rows if r.trainer})
    data['completion_rate']=data['stats']['completed_count']*100/len(rows) if rows else None
    data['delivery_rate']=data['stats']['certificates']*100/len(rows) if rows else None
    data['main_kpis']=[data['kpis'][1],data['kpis'][5],{'label':'Média de desempenho','value':data['average_final'],'decimal':True,'suffix':'/20','note':f"{data['final_grades_count']} participações concluídas com nota final",'icon':'stars'},data['kpis'][4]]
    for item,color,icon in zip(data['main_kpis'],[COLORS[1],COLORS[0],COLORS[2],COLORS[3]],['groups','how_to_reg','stars','timelapse']):item.update(color=color,icon=icon)
    data['secondary_kpis']=[data['kpis'][i] for i in (0,2,3,6,7)]
    number=lambda value:f'{value:.1f}'.replace('.',',')
    data['insights']=[]
    if rows:
        data['insights'].append(f"{len(rows)} participações, em {data['kpis'][2]['value']} turmas, totalizaram {number(total)} horas de participantes.")
        if data['area_bars'] and total:
            leading=data['area_bars'][0];data['insights'].append(f"{leading['label']} concentra {number(leading['percent'])}% das horas realizadas no universo seleccionado.")
        if data['stats']['certificates_pending']:data['insights'].append(f"{data['stats']['certificates_pending']} participações têm a entrega do certificado por confirmar.")
    else:data['insights'].append('Sem formações iniciadas no período seleccionado. Consulte também as entregas datadas e as conclusões no período.')
    missing_final=sum(r.status=='Concluído' and (r.grade is None or r.result=='Pendente') for r in rows)
    data['missing_final']=missing_final
    if missing_final:data['insights'].append(f'{missing_final} participações concluídas têm nota final ou resultado por completar.')
    data['previous']=None
    if start and base is not None:
        prev_start,prev_end=previous_bounds(start,end,selected.get('period',''))
        previous=list(base.filter(start__range=(prev_start,prev_end)));ps=statistics(previous)
        change=(total-ps['hours'])*100/ps['hours'] if ps['hours'] else None
        data['previous']={'start':prev_start,'end':prev_end,'hours':ps['hours'],'participations':len(previous),'change':change}
    operational=list(base) if base is not None else rows
    deliveries=[r for r in operational if r.certified is True and r.certificate_delivery_date and (not start or start<=r.certificate_delivery_date<=end)]
    endings=[r for r in operational if r.status=='Concluído' and (not start or start<=r.end<=end)]
    data.update(deliveries=deliveries,ended=endings,delivery_dates_missing=sum(r.certified is True and not r.certificate_delivery_date for r in operational),delivery_href=drill('certificate','yes','Certificados entregues'))
    return data

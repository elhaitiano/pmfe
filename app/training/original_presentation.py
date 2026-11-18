"""Approved presentation fed exclusively by the reconciled historical data."""
from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import quote
from . import consolidation as service
from .models import TrainingDocumentState,HistoricalDocumentaryParticipation
from .analytics import distribution as area_distribution


def rows_for_display(rows):
    rows=list(rows)
    legacy={i for p in rows for i in p.legacy_training_ids};documentary={i for p in rows for i in p.documentary_participation_ids}
    states={s.training_id:s for s in TrainingDocumentState.objects.filter(training_id__in=legacy)}
    docs={s.pk:s for s in HistoricalDocumentaryParticipation.objects.filter(pk__in=documentary)}
    shown=[]
    for p in rows:
        e=p.edition
        certificates=[states[i] for i in p.legacy_training_ids if i in states]+[docs[i] for i in p.documentary_participation_ids if i in docs]
        delivery=True if any(s.learner_delivered is True for s in certificates) else False if certificates and all(s.learner_delivered is False for s in certificates) else None
        grades=[g for g in p.grades if g['stage']=='final'] or [g for g in p.grades if g['stage']=='certificate_mention'] or p.grades
        label='; '.join(g['value']+'/'+g['scale']+((' · '+g['phase']) if g.get('phase') else '') for g in grades)
        shown.append(SimpleNamespace(pk=p.pk,employee=p.employee,title=e.title,start=e.start,end=e.end,location=e.location,trainer=e.trainer,provider=e.provider,
            area=e.course.area if e.course_id else '',grade=None,evaluation={'available':bool(grades),'label':label,'calculated':False},
            source_achievement=None,completed_hours=p.attended_hours,documented_hours=p.attended_hours,attendance_confirmed=p.attendance_complete,
            attendance_percent=p.attendance_percent,result=p.result or 'Resultado não registado',status='Concluído' if p.completed is True else 'Inscrição histórica' if p.present_count is None and not grades else 'Registo histórico',
            certificate_label='Entregue' if delivery is True else 'Não entregue' if delivery is False else 'Sem confirmação registada',certificate_delivery_date=None,certificate_reference='',
            observations='',consolidated=True,delivery=delivery,original=p,status_label='Concluído' if p.completed is True else 'Conclusão não documentada'))
    return shown


def profile(rows):
    rows=list(rows);summary=service.stats(rows);shown=rows_for_display(rows)
    delivered=sum(t.delivery is True for t in shown);pending=sum(t.delivery is None for t in shown)
    stats={**summary,'total':len(rows),'hours':summary['attended_hours'],'learner_delivery_value':delivered if any(t.delivery is not None for t in shown) else None,'certificates_pending':pending}
    distribution=area_distribution(shown);total=summary['attended_hours']
    stats.update(unclassified_participations=sum(not t.area for t in shown),unclassified_hours=sum((t.completed_hours or Decimal(0) for t in shown if not t.area),Decimal(0)))
    competencies=[]
    for t in shown:
        final=[g for g in t.original.grades if g['stage']=='final']
        numeric=final[0] if len(final)==1 else None
        competencies.append({'training':t,'percent':Decimal(numeric['value'])*100/Decimal(numeric['scale']) if numeric else None})
    return {'stats':stats,'trainings':shown,'distribution':distribution,'competencies':competencies[:6],'has_hours':bool(total),
        'unconfirmed_certificates':pending,'history_total':len(rows),'latest_location':shown[0].location if shown else ''}


def dashboard(rows):
    rows=list(rows);data=profile(rows);s=data['stats'];by_direction=defaultdict(list);months=defaultdict(Decimal)
    for p in rows:
        by_direction[p.employee.direction or 'Direção não indicada'].append(p)
        if p.edition.start and p.attended_hours is not None:months[p.edition.start.strftime('%Y-%m')]+=p.attended_hours
    def comparable(items):
        values=[]
        for p in items:
            grades=[g for g in p.grades if Decimal(g['scale'])==20 and g['stage'] in ['final','certificate_mention']]
            if len(grades)==1:values.append(Decimal(grades[0]['value']))
        return values
    directions=[];by_person=defaultdict(list)
    for p in rows:by_person[p.employee_id].append(p)
    talents=[]
    for items in by_person.values():
        grades=comparable([p for p in items if p.completed is True])
        if not grades:continue
        average=sum(grades)/len(grades);metrics=service.stats(items)
        talents.append({'employee':items[0].employee,'average':average,'graded':len(grades),'stats':{**metrics,'hours':metrics['attended_hours']},'href':'/consolidacao/historicos/?employee='+str(items[0].employee_id)})
    talents.sort(key=lambda p:(-p['average'],p['employee'].number))
    scatter=[{'name':t['employee'].name,'average':t['average'],'attendance':t['stats']['attendance'],'x':55+t['stats']['attendance']*Decimal('4.9'),'y':220-t['average']*Decimal('9.75'),'radius':5,'href':t['href']} for t in talents if t['stats']['attendance'] is not None]
    for label,items in sorted(by_direction.items()):
        totals=service.stats(items);people=service.workers().filter(direction=label).count()
        grades=comparable(items)
        directions.append({'label':label,'average':sum(grades)/len(grades) if grades else None,'hours':totals['attended_hours'],'present':totals['people_with_attendance'],'total':people,
            'attendance':totals['attendance'],'coverage':totals['people_with_attendance']*100/people if people else 0,'href':'/consolidacao/historicos/?direction='+quote(label)})
    peak=max(months.values(),default=Decimal(1)) or Decimal(1)
    data.update(period_label='Histórico consolidado',has_rows=bool(rows),directions_active=len(by_direction),locations=len({p.edition.location for p in rows if p.edition.location}),
        trainers=len({p.edition.trainer for p in rows if p.edition.trainer}),completion_rate=s['completed']*100/len(rows) if rows else None,directions_table=directions,talents=talents[:10],scatter=scatter,
        main_kpis=[{'label':'Formandos com presença comprovada','value':s['people_with_attendance'],'icon':'groups','color':'#41e4c0','note':'Funcionários distintos'},
            {'label':'Horas frequentadas','value':s['attended_hours'],'decimal':True,'suffix':' H','icon':'schedule','color':'#4a8eff','note':'Apenas horas comprovadas'},
            {'label':'Participações / inscrições','value':s['total'],'icon':'school','color':'#adc7ff','note':'Registos consolidados por edição'},
            {'label':'Assiduidade','value':s['attendance'],'decimal':True,'suffix':'%','icon':'event_available','color':'#41e4c0','note':'Somente com cobertura suficiente'}],
        area_bars=[{**item,'value':item['hours'],'href':'/consolidacao/historicos/'} for item in data['distribution']],
        area_donut=[{**item,'value':item['hours'],'href':'/consolidacao/historicos/'} for item in data['distribution']],
        timeline_title='Evolução das horas frequentadas',timeline_bars=[{'label':key,'value':value,'width':value*100/peak,'href':'/consolidacao/historicos/?year='+key[:4]} for key,value in sorted(months.items())],
        status_bars=[{'label':'Conclusão declarada na fonte','value':s['completed'],'width':s['completed']*100/len(rows) if rows else 0,'href':'/consolidacao/historicos/'},
            {'label':'Outros registos históricos','value':len(rows)-s['completed'],'width':(len(rows)-s['completed'])*100/len(rows) if rows else 0,'href':'/consolidacao/historicos/'}],
        results=[{'label':'Resultados / menções documentados','value':s['with_result'],'href':'/consolidacao/historicos/'},{'label':'Com notas nas escalas originais','value':s['with_grades'],'href':'/consolidacao/historicos/'}],
        insights=['Notas conservadas nas escalas originais, sem resultados simulados.','Inscrição, presença, conclusão e entrega do certificado são estados distintos.'],
        ended=[p for p in rows if p.completed is True],deliveries=[],delivery_dates_missing=s['learner_delivery_value'])
    return data

"""Read-only historical navigation. Never creates TrainingGroup or Enrollment."""
import hashlib,json,os
from collections import Counter,defaultdict
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from .models import Training,Course

AGE_BANDS=[('u25','Até 24'),('25','25–34'),('35','35–44'),('45','45–54'),('55','55–64'),('65','65+'),('unknown','Não informado')]
def manifest():
    from .private_data import load
    return load('historical')

def enabled():return getattr(settings,'PMF_HISTORICAL_ENABLED',os.environ.get('PMF_HISTORICAL_ENABLED')=='1')

def signature(t):
    values=[t.source_key,t.employee_id,t.title,t.start.isoformat(),t.end.isoformat(),t.days,float(t.hours_per_day) if t.hours_per_day%1 else int(t.hours_per_day),json.dumps(t.source_data,ensure_ascii=False)]
    # SQLite's JSON text is retained verbatim for byte-exact provenance matching.
    values[-1]=getattr(t,'source_json_text',values[-1])
    values.extend([t.trainer,t.location])
    return hashlib.sha256(json.dumps(values,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()

def age_band(employee,reference):
    birth=employee.birth_date
    validation=str(employee.source_data.get('Validação Cadastro EPAL',''))
    original=employee.source_data.get('Data de Nascimeto')
    if not birth or not validation.startswith('Definitivo') or str(original or '')[:10]!=birth.isoformat() or birth>reference:return 'unknown'
    age=reference.year-birth.year-((reference.month,reference.day)<(birth.month,birth.day))
    return 'u25' if age<25 else '25' if age<35 else '35' if age<45 else '45' if age<55 else '55' if age<65 else '65'

def records():
    from django.db.models.functions import Cast
    from django.db.models import TextField
    m=manifest();result=[]
    # Fixed number of queries, including employee and documentary supplement.
    courses={}
    for c in Course.objects.filter(source_system='epal-proagua-catalogo'):
        courses.setdefault(c.source_action_id,[]).append(c)
    rows=Training.objects.exclude(source_key__in=m['excluded_keys']).select_related('employee','document_state').annotate(source_json_text=Cast('source_data',TextField()))
    for t in rows:
        b=m['bindings'].get(t.source_key)
        cs=courses.get(b['action_id'],[]) if b else []
        bound=bool(b and len(cs)==1 and signature(t)==b['signature'] and hashlib.sha256(str(t.employee.number).strip().encode()).hexdigest()==b.get('employee_number_sha256'))
        state=getattr(t,'document_state',None)
        learner=state.learner_delivered if state and state.learner_delivered is not None else t.certified
        result.append({'training':t,'binding':b if bound else None,'edition':m['editions'][b['edition']] if bound else None,'course':cs[0] if bound else None,'level':b['level'] if bound else 'unmatched','age_band':age_band(t.employee,t.start),'state':state,'learner_delivery':learner})
    from .source_achievement import attach
    attach([r['training'] for r in result])
    for row in result:row['achievement']=row['training'].source_achievement
    return result

def filtered(items,params):
    allowed={k:str(params.get(k,'')).strip() for k in ('year','direction','action','edition','age_band','level')}
    for key,val in allowed.items():
        if not val:continue
        if key=='year':items=[r for r in items if str(r['training'].start.year)==val]
        elif key=='direction':items=[r for r in items if r['training'].employee.direction==val]
        elif key=='age_band':items=[r for r in items if r['age_band']==val]
        elif key=='level':items=[r for r in items if r['level']==val]
        elif key=='action':items=[r for r in items if r['binding'] and r['binding']['action_id']==val]
        elif key=='edition':items=[r for r in items if r['binding'] and r['binding']['edition']==val]
    return items

def metrics(items):
    from .analytics import statistics,confirmed as attendance_confirmed
    documentary=statistics([r['training'] for r in items])
    confirmed=[r for r in items if r['level']=='confirmed']
    attended=[r['training'] for r in items if attendance_confirmed(r['training'])]
    days=sum(t.days for t in attended);presences=sum(t.attendance for t in attended)
    grades=[r['training'].grade for r in items if r['training'].status=='Concluído' and r['training'].grade is not None]
    segments=defaultdict(set)
    for r in confirmed:segments[(r['binding']['edition'],r['binding']['phase'])].add((r['training'].days,r['training'].hours_per_day))
    segment_hours=sum((next(iter(values))[0]*next(iter(values))[1] for values in segments.values()),Decimal(0)) if all(len(values)==1 for values in segments.values()) else None
    return {**documentary,'consultation_groups':len({r['binding']['edition'] for r in items if r['binding']}),'participations':len(items),'people':len({r['training'].employee_id for r in items}),'groups':len({r['binding']['edition'] for r in confirmed}),'confirmed':len(confirmed),'probable':sum(r['level']=='probable' for r in items),'unmatched':sum(r['level']=='unmatched' for r in items),'completed':sum(r['training'].status=='Concluído' for r in items),'documented_completed':sum(r['training'].status=='Concluído' and r['level']!='unmatched' for r in items),'attendance_coverage':len(attended),'attendance_days':days,'attendance_presences':presences,'attendance':Decimal(presences)*100/days if days else None,'planned_participant_hours':sum((r['training'].planned_hours for r in items),Decimal(0)),'actual_participant_hours':sum((t.completed_hours for t in attended),Decimal(0)) if attended else None,'documented_segment_hours':segment_hours,'grades':len(grades),'average':sum(grades)/len(grades) if grades else None,'issued':sum(bool(r['state'] and r['state'].issued is True) for r in items),'learner_delivered':sum(r['learner_delivery'] is True for r in items),'hr_delivered':sum(bool(r['state'] and r['state'].hr_delivered is True) for r in items),'ages':[{'key':k,'label':label,'count':sum(r['age_band']==k for r in items),'percent':sum(r['age_band']==k for r in items)*100/len(items) if items else 0} for k,label in AGE_BANDS]}

def presentation_labels():
    # Stable numbering per action, independent of filters or attendance.
    numbers={};per_action=Counter()
    for key,edition in sorted(manifest()['editions'].items()):
        action=edition.get('action_id','')
        per_action[action]+=1;numbers[key]=per_action[action]
    return {key:e.get('documented_group') or f'Grupo {numbers[key]}' for key,e in manifest()['editions'].items()}

def editions(items):
    groups=defaultdict(list)
    for r in items:
        if r['binding']:groups[r['binding']['edition']].append(r)
    labels=presentation_labels()
    return [{'edition':{**group[0]['edition'],'presentation_label':labels[key]},'stats':metrics(group)} for key,group in sorted(groups.items())]

def attach_counts(courses):
    counts=defaultdict(set)
    for r in records():
        if r['binding']:counts[r['course'].pk].add(r['binding']['edition'])
    for c in courses:c.historical_count=len(counts[c.pk])

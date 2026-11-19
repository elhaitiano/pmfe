"""Reconcile explicit user edits with the shared historical facts after successful writes."""
import hashlib,json
from decimal import Decimal
from django.db import transaction
from django.db.models import F,Q
from django.utils import timezone


def inputs(t):
    def normalized(value):return format(value.normalize(),'f') if isinstance(value,Decimal) else str(value) if value is not None else None
    return {field:normalized(getattr(t,field)) for field in
        ['days','hours_per_day','attendance','attendance_confirmed','grade','result','status','title','start','end','course_id','group_id','location','trainer',
         'certified','certificate_delivery_date','certificate_issued','certificate_issue_date','certificate_hr_delivered','certificate_hr_delivery_date']}


@transaction.atomic
def reconcile(actor):
    from .models import Training,ConsolidationRun,ConsolidatedEdition,ConsolidatedParticipation,HistoricalInterfaceLink,AuditEvent
    if not ConsolidationRun.objects.filter(active=True).exists():return
    changed=Training.objects.filter(Q(source_data___EPAL_CONSOLIDATED_FACTS__sync_version__isnull=True)|~Q(version=F('source_data___EPAL_CONSOLIDATED_FACTS__sync_version')))
    for t in changed.select_related('employee','course','group'):
        previous=(t.source_data or {}).get('_EPAL_CONSOLIDATED_FACTS',{});current=inputs(t)
        link=HistoricalInterfaceLink.objects.filter(training=t).select_related('participation','participation__edition').first()
        if link and previous.get('inputs')==current:
            previous['sync_version']=t.version;t.source_data['_EPAL_CONSOLIDATED_FACTS']=previous
            Training.objects.filter(pk=t.pk).update(source_data=t.source_data);continue
        if link:p=link.participation;e=p.edition
        else:
            identity=[t.course_id,t.title,str(t.start),str(t.end),t.group_id,t.location,t.trainer]
            key=hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            e,_=ConsolidatedEdition.objects.get_or_create(source_key='manual:'+key,defaults={'course':t.course,'course_reference':t.source_course_code or (t.course.code if t.course_id else '') or 'TITLE:'+t.title,
                'title':t.title,'group_label':str(t.group.number) if t.group_id else '', 'start':t.start,'end':t.end,'location':t.location,'trainer':t.trainer,
                'provider':t.provider,'declared_days':t.days,'hours_per_day':t.hours_per_day,'total_hours':t.planned_hours,'evidence':{'origin':'Registo efetivo na plataforma','actor':actor}})
            key=hashlib.sha256((str(t.employee_id)+':'+e.source_key).encode()).hexdigest()
            p,created=ConsolidatedParticipation.objects.get_or_create(source_key='manual:'+key,defaults={'employee':t.employee,'edition':e})
            if HistoricalInterfaceLink.objects.filter(participation=p).exists():raise ValueError('Esta participação já está registada.')
            HistoricalInterfaceLink.objects.create(participation=p,training=t)
        evidence=dict(p.evidence);history=evidence.setdefault('interface_changes',[])
        history.append({'actor':actor,'timestamp':timezone.now().isoformat(),'before':previous.get('inputs'), 'after':current,'previous_grades':p.grades,'previous_facts':previous})
        p.evidence=evidence
        p.active=True
        if not e.active:e.active=True;e.save(update_fields=['active'])
        dimensional=['title','start','end','course_id','group_id','location','trainer','days','hours_per_day']
        if link and any(current.get(k)!=previous.get('inputs',{}).get(k) for k in dimensional):
            key=hashlib.sha256(json.dumps([current.get(k) for k in dimensional]).encode()).hexdigest()
            e,_=ConsolidatedEdition.objects.get_or_create(source_key='manual:'+key,defaults={'course':t.course,'course_reference':t.source_course_code or (t.course.code if t.course_id else '') or 'TITLE:'+t.title,
                'title':t.title,'group_label':str(t.group.number) if t.group_id else previous.get('group',''),'start':t.start,'end':t.end,'location':t.location,'trainer':t.trainer,
                'provider':t.provider,'declared_days':t.days,'hours_per_day':t.hours_per_day,'total_hours':t.days*t.hours_per_day if t.days is not None and t.hours_per_day is not None else e.total_hours,'evidence':{'origin':'Correção efetiva na plataforma','actor':actor}})
            p.edition=e
        if current.get('grade')!=previous.get('inputs',{}).get('grade') and t.grade is not None:
            p.grades=[g for g in p.grades if g['stage']!='final']+[{'stage':'final','value':str(t.grade),'scale':'20','claims':[{'source':'Registo efetivo na plataforma','actor':actor,'training':t.pk,'revision':t.version}]}]
        denominator=t.days if t.days is not None else t.attendance_days
        confirmed=bool(t.attendance_confirmed and t.attendance is not None and denominator is not None and denominator>0)
        presence_changed=not link or any(current.get(k)!=previous.get('inputs',{}).get(k) for k in ['attendance','attendance_confirmed','days'])
        if presence_changed:
            p.present_count=t.attendance if confirmed else None
            p.absent_count=int(denominator-t.attendance) if confirmed and denominator==int(denominator) else None
            p.attendance_complete=confirmed;p.attendance_percent=Decimal(t.attendance)*100/denominator if confirmed else None
        if presence_changed or current.get('hours_per_day')!=previous.get('inputs',{}).get('hours_per_day'):
            p.attended_hours=Decimal(t.attendance)*t.hours_per_day if confirmed and t.hours_per_day is not None else p.edition.total_hours if confirmed and p.absent_count==0 else None
        if current.get('result')!=previous.get('inputs',{}).get('result'):p.result=t.result if t.result!='Pendente' else ''
        if current.get('status')!=previous.get('inputs',{}).get('status'):p.completed=True if t.status=='Concluído' else None
        p.save()
        facts={**previous,'inputs':current,'sync_version':t.version,'edition':p.edition.source_key,'participation':p.source_key,'group':p.edition.group_label,
            'total_hours':str(p.edition.total_hours) if p.edition.total_hours is not None else None,'attendance_percent':str(p.attendance_percent) if p.attendance_percent is not None else None,
            'attended_hours':str(p.attended_hours) if p.attended_hours is not None else None,'absent_count':p.absent_count,'result':p.result,'grades':p.grades}
        t.source_data={**t.source_data,'_EPAL_CONSOLIDATED_FACTS':facts};Training.objects.filter(pk=t.pk).update(source_data=t.source_data)
        AuditEvent.objects.create(actor=actor,action='HISTORICAL_INTERFACE_SYNC',detail=json.dumps({'training':t.pk,'participation':p.pk,'revision':t.version},ensure_ascii=False))


class InterfaceSyncMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if request.method!='POST':return self.get_response(request)
        with transaction.atomic():
            response=self.get_response(request)
            if not getattr(request,'group_validation_managed',False) and not getattr(request,'formation_survey_managed',False) and response.status_code in [200,201,302,303] and getattr(request,'user',None) and request.user.is_authenticated and request.user.is_staff:
                reconcile(request.user.username)
            return response

"""Derived documentary presentation, never approval and never persistence."""
import json
from decimal import Decimal,InvalidOperation
from django.core.exceptions import ValidationError
from .catalogue_import import digest
from .models import AuditEvent

def decimal(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation,ValueError,TypeError):return None

def evidence(database='default'):
    result={}
    events=AuditEvent.objects.using(database).filter(action__in=['DURATION_ACTION','CATALOGUE_CORRECTION']).order_by('-pk')
    for event in events:
        try:d=json.loads(event.detail)
        except (ValueError,TypeError):continue
        if not isinstance(d,dict):continue
        if event.action=='DURATION_ACTION':
            pk=d.get('pk');record={'days':d.get('days_after'),'hours':d.get('hours_after'),'revision':d.get('revision_after'),'source_action_id':d.get('source_action_id'),'source_digest':None,'reference':d.get('reference'),'reason':d.get('reason'),'event_id':event.pk}
        else:
            after=d.get('after',{})
            if not isinstance(after,dict):continue
            pk=d.get('course_id');record={'days':after.get('default_days'),'hours':after.get('default_hours'),'revision':after.get('revision'),'source_action_id':d.get('source_action_id'),'source_digest':after.get('source_digest'),'reference':d.get('reference'),'reason':d.get('justification'),'event_id':event.pk}
        if pk and pk not in result:result[pk]=record
    return result

def profile(course,proof=None):
    meta=course.source_metadata if isinstance(course.source_metadata,dict) else {}
    intact=bool(meta and course.source_digest and digest(meta)==course.source_digest and meta.get('id')==course.source_action_id and course.source_system=='epal-proagua-catalogo')
    designation=bool(intact and course.title.strip() and meta.get('title')==course.title and course.validation_status!='blocked' and course.source_action_id!='HC-RDSC')
    days=decimal(course.default_days);hours=decimal(course.default_hours)
    valid_duration=days is not None and days>0 and days==days.to_integral_value() and hours is not None and hours>0
    if valid_duration:
        try:
            for field in ('default_days','default_hours'):course._meta.get_field(field).clean(getattr(course,field),course)
        except (ValidationError,ValueError,ArithmeticError):valid_duration=False
    audited=bool(valid_duration and proof and proof.get('revision')==course.revision and proof.get('source_action_id')==course.source_action_id and decimal(proof.get('days'))==days and decimal(proof.get('hours'))==hours and proof.get('reference') and proof.get('reason') and (proof.get('source_digest') in (None,course.source_digest)))
    core=bool(designation and audited)
    pending=[]
    if not intact:pending.append('Documentação de origem por confirmar')
    if not designation:pending.append('Designação oficial por confirmar')
    if not audited:pending.append('Duração diária e respectiva fundamentação por confirmar')
    if not course.internal_code:pending.append('Código interno aprovado por confirmar')
    if not course.area:pending.append('Área de formação por definir')
    return {'core_verified':core,'label':'Dados verificados — designação e duração' if core else 'Verificação técnica incompleta','designation_verified':designation,'duration_verified':audited,'source_intact':intact,'duration_days':course.default_days if audited else None,'duration_hours':course.default_hours if audited else None,'duration_total':days*hours if audited else None,'event_id':proof.get('event_id') if audited else None,'reference':proof.get('reference') if audited else '', 'pending':pending,'all_technical_fields_complete':core and bool(course.internal_code) and bool(course.area),'formal_state':course.get_validation_status_display(),'formal_approval_proven':False,'warning':'Verificação técnica limitada aos campos indicados; não representa aprovação oficial nem autoriza criar grupos operacionais.'}

def attach(courses):
    rows=list(courses)
    proofs=evidence(rows[0]._state.db or 'default') if rows else {}
    for course in rows:course.technical=profile(course,proofs.get(course.pk))
    return rows

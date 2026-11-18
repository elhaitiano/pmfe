"""One read-only presentation policy. Never writes or homologates source facts."""
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from django.db.models import F
from django.utils import timezone
from .models import Training,HistoricalInterfaceLink,TrainingDocumentState
from .certificate_register import row_summary

LABELS={'participations':'Participações','people':'Técnicos únicos com registo','completed_recorded':'Conclusões registadas no histórico','completed_confirmed':'Conclusões comprovadas','completed_people':'Técnicos com conclusão comprovada','expected':'Certificados previstos por emitir','issued':'Certificados emitidos','delivered':'Certificados entregues','verify':'Certificados por verificar','review':'Participações em conferência documental','pending_criteria':'Conclusões com critérios de certificação por homologar'}

def review_index():
    path=Path(__file__).parent/'resources/regularization_review.json'
    if not path.exists():return {}
    return json.loads(path.read_text('utf8'))['training_ids']

def completion_assessment(p,t=None,group_key=None):
    """Reusable for both canonical coverage rows and individual Training views."""
    from .group_validation import academic
    e=p.edition if p else None;course=e.course if e and e.course_id else t.course if t and t.course_id else None
    evidence=getattr(p,'_operational_evidence',None) if p else None
    if evidence is None:evidence=(p.evidence or {}) if p else {}
    issues=evidence.get('issues') or [];end=e.end if e else t.end
    present=p.present_count if p else t.attendance;known=p.attendance_complete if p else t.attendance_confirmed
    hours=p.attended_hours if p else t.completed_hours;recorded=p.completed is True if p else t.status=='Concluído'
    key=group_key or ('group:'+str(getattr(p,'_native_group_id')) if p and getattr(p,'_native_group_id',None) else 'edition:'+str(p.edition_id) if p else 'group:'+str(t.group_id) if t.group_id else '')
    rule=(course.source_metadata or {}).get('_approved_group_validation_criteria') if course else None
    if p and isinstance(rule,dict) and key not in rule.get('groups',[]) and 'edition:'+str(p.edition_id) in rule.get('groups',[]):key='edition:'+str(p.edition_id)
    scoped=bool(isinstance(rule,dict) and rule.get('approval_reference') and rule.get('approved_by') and key in rule.get('groups',[]))
    status,academic_reasons=academic(t,p,course,key) if scoped else ('Por validar',['Critérios académicos/certificação ainda não homologados para este grupo.'])
    reasons=[]
    if issues:reasons.append('Divergências nas fontes por resolver.')
    if end is None or end>timezone.localdate():reasons.append('Período por confirmar ou ainda não terminado.')
    if known and present==0:reasons.append('Ausência integral: conclusão requer conferência.')
    if not known or present is None:reasons.append('Assiduidade desconhecida ou incompleta; não equivale a falta.')
    if hours is None:reasons.append('Horas efectivamente frequentadas por confirmar.')
    elif hours<=0 and (present or 0)>0:reasons.append('Presenças positivas sem horas frequentadas positivas.')
    if p and known:
        if present is None or p.absent_count is None or present+p.absent_count<=0:reasons.append('Denominador de assiduidade não comprovado.')
        elif p.attendance_percent is None or abs(Decimal(present)*100/Decimal(present+p.absent_count)-p.attendance_percent)>Decimal('.011'):reasons.append('Percentagem de assiduidade por conferir.')
    if recorded and ((p and p.result=='Reprovado') or t and t.result=='Reprovado'):reasons.append('Resultado histórico de reprovação em conflito com conclusão; requer revisão.')
    if scoped and rule.get('non_classifying_documented') is True and rule.get('attendance_min_percent') is not None:
        from .group_validation import number
        limit=number(rule['attendance_min_percent']);percentage=(Decimal(p.present_count)*100/Decimal(p.present_count+p.absent_count) if p and p.present_count is not None and p.absent_count is not None and p.present_count+p.absent_count>0 else None) if p else t.attendance_percent
        if limit is None or not 0<=limit<=100:reasons.append('Mínimo de assiduidade homologado inválido.')
        elif percentage is None or percentage<limit:reasons.append('Assiduidade não satisfaz o mínimo homologado.')
    if scoped and status not in ['Aprovado','Não aplicável']:reasons+=academic_reasons
    positive=known and (present or 0)>0 and hours is not None and hours>0
    source_proof=bool(evidence.get('sources') or evidence.get('supplement_sources') or evidence.get('interface_changes')) if p else known
    confirmed=bool(not reasons and positive and ((recorded and (source_proof or p is not None)) or scoped and status in ['Aprovado','Não aplicável']))
    label='Concluído' if confirmed else 'Não concluído' if (p and p.completed is False) or t and t.status=='Desistência' else 'Inscrito' if t and t.status=='Planeado' else 'Em curso' if t and t.status=='Em Curso' else 'Por validar'
    return {'recorded_completion':recorded,'confirmed_completion':confirmed,'completion':label,'academic':status,'criteria_scoped':scoped,'reasons':reasons,'academic_reasons':academic_reasons,'hours':hours,'attendance':p.attendance_percent if p else t.attendance_percent}

def assess(t,p=None,state=None,review=None):
    """Recorded conclusion and evidence-confirmed conclusion are distinct facts."""
    from .group_validation import academic
    facts=(t.indicator_source_data or {}).get('_EPAL_CONSOLIDATED_FACTS') or {}
    source=getattr(t,'_operational_source',None)
    if source is None:source=t.source_data or {}
    common=completion_assessment(p,t,'group:'+str(t.group_id) if t.group_id else None)
    reasons=list(common['reasons'])
    if p and p.employee_id!=t.employee_id:reasons.append('Identidade da participação divergente.')
    if p and p.edition.course_id!=t.course_id:reasons.append('Formação da participação divergente.')
    course=t.course if t.course_id else None;scoped=common['criteria_scoped'];academic_status=common['academic'];academic_reasons=common['academic_reasons']
    confirmed=common['confirmed_completion'] and not reasons;recorded=common['recorded_completion'];completed_label=common['completion'] if confirmed or not common['confirmed_completion'] else 'Por validar'
    # Explicit legacy eligibility remains supported. Otherwise eligibility must
    # follow the already approved, group-scoped rule; no arbitrary marker is required.
    explicit=source.get('certificate_eligible') is True
    minimum=((course.source_metadata or {}).get('certificate_rules') or {}).get('minimum_grade') if course else None
    if minimum is not None:
        try:explicit=explicit and t.presentation_grade is not None and Decimal(str(t.presentation_grade))>=Decimal(str(minimum))
        except (ValueError,TypeError,ArithmeticError):explicit=False
    eligible=bool(confirmed and ((scoped and academic_status in ['Aprovado','Não aplicável']) or explicit))
    cs=row_summary(t)
    delivered=cs.get('delivered_count',int(t.delivery_reported))
    issued=cs.get('issued_count',int(t.certificate_issued is True))
    if state and not cs.get('same_certificate_as'):
        issued=max(issued,int(state.issued is True));delivered=max(delivered,int(state.learner_delivered is True))
    from .analytics import EXCLUDED_KEYS
    from . import data_validation
    excluded=bool(t.source_key in EXCLUDED_KEYS or data_validation.enabled() and data_validation.policy().duplicate(t.pk))
    same_certificate=cs.get('same_certificate_as')
    eligible_count=int(eligible and not excluded and not same_certificate)
    expected=bool(eligible_count and not issued and not delivered)
    certificate='Entregue' if delivered else 'Emitido' if issued else 'Previsto' if expected else 'Por verificar'
    return {'indicator_excluded':excluded,'certificate_counted_elsewhere':bool(same_certificate),'eligible_count':eligible_count,'training_id':t.pk,'employee_id':t.employee_id,'title':t.title,'recorded_completion':recorded,'confirmed_completion':confirmed,'completion':completed_label,'academic':academic_status,'certificate':certificate,'expected':int(expected),'issued':issued,'delivered':delivered,'verify':int(not expected and not issued and not delivered),'eligible':eligible,'criteria_scoped':scoped,'reasons':reasons+([] if eligible or issued or delivered else academic_reasons),'review':(review or {}).get(str(t.pk),[]),'hours':common['hours'],'attendance':common['attendance'],'protected_source_unchanged':True}

def from_participations(participations):
    from .models import ConsolidatedParticipation
    ps=list(participations)
    bindings=dict(HistoricalInterfaceLink.objects.filter(participation_id__in=[p.pk for p in ps]).values_list('participation_id','training_id'))
    mapped={r['training_id']:r for r in rows(training_ids=bindings.values())}
    fallback={p.pk:p for p in ConsolidatedParticipation.objects.filter(pk__in=[p.pk for p in ps if bindings.get(p.pk) not in mapped]).select_related('edition__course')}
    result=[]
    for p in ps:
        if bindings.get(p.pk) in mapped:result.append(mapped[bindings[p.pk]]);continue
        p=fallback[p.pk];common=completion_assessment(p)
        eligible=common['confirmed_completion'] and common['criteria_scoped'] and common['academic'] in ['Aprovado','Não aplicável']
        result.append({**common,'training_id':None,'participation_id':p.pk,'employee_id':p.employee_id,'title':p.edition.title,'eligible_count':int(eligible),'indicator_excluded':False,'certificate_counted_elsewhere':False,'certificate':'Previsto' if eligible else 'Por verificar','expected':int(eligible),'issued':0,'delivered':0,'verify':int(not eligible),'review':[]})
    return result

def rows(training_ids=None,employee_id=None):
    from .query_helpers import dashboard_summary
    qs=Training.objects.all()
    if training_ids is not None:qs=qs.filter(pk__in=list(training_ids))
    if employee_id is not None:qs=qs.filter(employee_id=employee_id)
    # Full source is loaded once in one query, not lazily per participation.
    ts=list(qs.select_related('course').order_by('pk'))
    attach(ts)
    return [t.operational for t in ts]

def attach(trainings):
    from .models import Course
    from django.db.models.functions import JSONObject
    ts=list(trainings)
    if not ts or all(hasattr(t,'operational') for t in ts):return ts
    ids=[t.pk for t in ts]
    qs=HistoricalInterfaceLink.objects.filter(training_id__in=ids).select_related('participation__edition').defer('participation__evidence','participation__legacy_training_ids','participation__documentary_participation_ids').annotate(op_evidence=JSONObject(issues=F('participation__evidence__issues'),sources=F('participation__evidence__sources'),supplement_sources=F('participation__evidence__supplement_sources'),interface_changes=F('participation__evidence__interface_changes')))
    links={}
    for l in qs:l.participation._operational_evidence=l.op_evidence;links[l.training_id]=l.participation
    states={s.training_id:s for s in TrainingDocumentState.objects.filter(training_id__in=ids)}
    courses={c.pk:c for c in Course.objects.filter(pk__in={t.course_id for t in ts if t.course_id})}
    deferred=[t.pk for t in ts if 'source_data' in t.get_deferred_fields()]
    flags=dict(Training.objects.filter(pk__in=deferred).values_list('pk','source_data__certificate_eligible')) if deferred else {}
    review=review_index()
    for t in ts:
        t._operational_source={'certificate_eligible':flags.get(t.pk)} if t.pk in deferred else t.source_data or {}
        if t.course_id:t._state.fields_cache['course']=courses.get(t.course_id)
        p=links.get(t.pk)
        if p and p.edition.course_id:p.edition._state.fields_cache['course']=courses.get(p.edition.course_id)
        if p:p._native_group_id=t.group_id
        t.operational=assess(t,p,states.get(t.pk),review)
    return ts

def summary(items):
    physical=list(items)
    items=[r for r in physical if not r.get('indicator_excluded',False)]
    return {'eligible':sum(r.get('eligible_count',int(r.get('eligible',False))) for r in items),'proven_duplicates_excluded':len(physical)-len(items),'certificate_duplicates_excluded':sum(r.get('certificate_counted_elsewhere',False) for r in items),'participations':len(items),'people':len({r['employee_id'] for r in items}),'completed_recorded':sum(r['recorded_completion'] for r in items),'completed_confirmed':sum(r['confirmed_completion'] for r in items),'completed_people':len({r['employee_id'] for r in items if r['confirmed_completion']}),'expected':sum(r['expected'] for r in items),'issued':sum(r['issued'] for r in items),'delivered':sum(r['delivered'] for r in items),'verify':sum(r['verify'] for r in items),'review':sum(bool(r['review']) for r in items),'pending_criteria':sum(not r.get('eligible',False) and not r['criteria_scoped'] and not r['issued'] and not r['delivered'] and r['confirmed_completion'] for r in items)}

def context(request):
    match=getattr(request,'resolver_match',None)
    if not request.user.is_authenticated or not match:return {}
    if match.url_name in ['employee_data','employee_detail','employee_print'] and match.kwargs.get('pk'):
        entries=rows(employee_id=match.kwargs['pk'])
        return {'operational_summary':summary(entries),'operational_items':entries}
    return {}

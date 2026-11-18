"""Evidence-only group decisions, additive to historical facts. No schema changes."""
import copy
import hashlib
import json
from decimal import Decimal, InvalidOperation
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, OperationalError
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from .models import (Training, TrainingGroup, Enrollment, Course, AuditEvent,
                     ConsolidatedEdition, ConsolidatedParticipation,
                     HistoricalInterfaceLink, TrainingDocumentState, TrainingEvaluation)

PERMISSION = 'training.validate_training_group'
MARKER = '_group_validation_v1'
ACTION = 'GROUP_VALIDATION'
SALT = 'pmfe.group-validation.v1'
MAX_GROUPS = 10
MAX_COURSE_GROUPS = 100
MAX_COURSE_ROWS = 2000
MAX_ROWS = 500


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def authorize(user):
    if not user.is_authenticated or not user.is_active or not user.has_perms(
        [PERMISSION, 'training.view_training', 'training.view_employee']
    ):
        raise PermissionDenied('É necessária a permissão específica de validação colectiva.')


def keys_checked(keys,limit=MAX_GROUPS):
    if not isinstance(keys, (list, tuple)) or not keys or len(keys) > limit:
        raise ValidationError(f'Seleccione entre um e {limit} grupos.')
    normalized = []
    for key in keys:
        if not isinstance(key, str):raise ValidationError('Grupo inválido.')
        parts = key.split(':')
        if len(parts) != 2 or parts[0] not in ('edition', 'group') or not parts[1].isdecimal() or len(parts[1]) > 12:
            raise ValidationError('Grupo inválido.')
        normalized.append(parts[0]+':'+str(int(parts[1])))
    if len(set(normalized)) != len(normalized):raise ValidationError('Grupos repetidos.')
    return sorted(normalized)


def values(instance):
    return {f.attname: copy.deepcopy(getattr(instance, f.attname)) for f in instance._meta.concrete_fields}

def fingerprint_values(instance):
    """Read-only field projection for immediate serialization, never for mutation."""
    return {f.attname:getattr(instance,f.attname) for f in instance._meta.concrete_fields}


def loaded(query, lock):
    return list(query.select_for_update() if lock else query)


def prior_validations(ids,lock=False):
    """Protect existing signed supplements whose bindings include the raw row."""
    ids=set(ids);protected={};latest={}
    query=AuditEvent.objects.filter(action__in=['DATA_VERIFICATION','EPAL_EVALUATION_APPROVED','EVALUATION_CORRECTION']).order_by('pk')
    for event in loaded(query,lock):
        try:payload=json.loads(event.detail)
        except (TypeError,ValueError):continue
        if not isinstance(payload,dict):continue
        if event.action=='DATA_VERIFICATION':
            pk=payload.get('pk')
            if payload.get('kind')=='training' and pk in ids:latest[(pk,payload.get('dimension'))]=(event.pk,payload.get('state'))
        else:
            pk=payload.get('training_id')
            if pk in ids:protected.setdefault(pk,[]).append(event.pk)
    for (pk,dimension),(event_id,state) in latest.items():
        if state=='confirmed':protected.setdefault(pk,[]).append(event_id)
    return protected


def dependency_fingerprint(t,p,d,group,course,evaluation=None,enrollments=()):
    raw=fingerprint_values(t);raw['source_data']=dict(raw['source_data'])
    raw['source_data'].pop(MARKER,None);raw.pop('version',None)
    # Technical reconciliation revisions are not new documentary evidence.
    facts=raw['source_data'].get('_EPAL_CONSOLIDATED_FACTS')
    if isinstance(facts,dict):
        facts=dict(facts);facts.pop('sync_version',None)
        raw['source_data']['_EPAL_CONSOLIDATED_FACTS']=facts
    return digest({'training':raw,'employee':fingerprint_values(t.employee),'participation':fingerprint_values(p) if p else None,
                   'edition':fingerprint_values(p.edition) if p else None,'document':fingerprint_values(d) if d else None,
                   'evaluation':fingerprint_values(evaluation) if evaluation else None,'course':fingerprint_values(course),'group':fingerprint_values(group),
                   'enrollments':[fingerprint_values(en) for en in enrollments if en.employee_id==t.employee_id]})


def collect(key, lock=False):
    """Reuse exactly the membership bindings used by the existing group pages."""
    kind, pk = key.split(':'); pk = int(pk)
    model = ConsolidatedEdition if kind == 'edition' else TrainingGroup
    query = model.objects.filter(pk=pk)
    if kind == 'edition':query = query.filter(active=True)
    group = get_object_or_404(query.select_for_update() if lock else query)
    course = get_object_or_404(Course.objects.select_for_update() if lock else Course.objects, pk=group.course_id)
    pending = []
    if kind == 'edition':
        participations = loaded(ConsolidatedParticipation.objects.filter(edition=group, active=True).select_related('edition','employee').order_by('pk'), lock)
        links = loaded(HistoricalInterfaceLink.objects.filter(participation_id__in=[p.pk for p in participations]).order_by('pk'), lock)
        ids = [l.training_id for l in links]
        linked = {l.participation_id for l in links}
        pending = [{'name':p.employee.name, 'reason':'Participação sem ligação operacional confirmada.', 'employee_id':p.employee_id,
                    'correction_url':reverse('consolidated_participation',args=[p.pk])} for p in participations if p.pk not in linked]
        enrollments = []
    else:
        from .group_roster import group_trainings
        ids = list(group_trainings(group).values_list('pk', flat=True))
        enrollments = loaded(Enrollment.objects.filter(group=group).order_by('pk'), lock)
        linked_employees = set(Training.objects.filter(pk__in=ids).values_list('employee_id',flat=True))
        pending = [{'name':en.employee.name,'employee_id':en.employee_id,'reason':'Inscrição sem participação; inscrição não comprova conclusão.',
                    'correction_url':reverse('enrollment_edit',args=[group.pk,en.pk])} for en in enrollments if en.state=='Activa' and en.employee_id not in linked_employees]
        links = loaded(HistoricalInterfaceLink.objects.filter(training_id__in=ids).order_by('pk'), lock)
        participations = loaded(ConsolidatedParticipation.objects.filter(pk__in=[l.participation_id for l in links]).select_related('edition','employee').order_by('pk'), lock)
    if len(ids)+len(pending)>MAX_ROWS:raise ValidationError('O grupo excede 500 registos; valide em grupos menores.')
    trainings = loaded(Training.objects.filter(pk__in=ids).select_related('employee').order_by('pk'), lock)
    docs = loaded(TrainingDocumentState.objects.filter(training_id__in=ids).order_by('pk'), lock)
    evaluations = loaded(TrainingEvaluation.objects.filter(training_id__in=ids).order_by('pk'), lock)
    cp_by_id = {p.pk:p for p in participations}
    participation_by_training = {l.training_id:cp_by_id[l.participation_id] for l in links if l.participation_id in cp_by_id}
    doc_by_training = {d.training_id:d for d in docs}
    evaluation_by_training = {e.training_id:e for e in evaluations}
    protected=prior_validations(ids,lock)
    # Include the imported membership events: later edits must invalidate a preview.
    binding_events = list(AuditEvent.objects.filter(action='IMPORT_GROUPS_70_V1').values('pk','detail')) if kind=='group' else []
    base = {'key':key,'group':values(group),'course':values(course),'pending':pending,
            'enrollments':[values(e) for e in enrollments], 'links':[values(l) for l in links], 'binding_events':binding_events}
    rows = []
    for t in trainings:
        p = participation_by_training.get(t.pk); d = doc_by_training.get(t.pk)
        evaluation = evaluation_by_training.get(t.pk)
        fingerprint=dependency_fingerprint(t,p,d,group,course,evaluation,enrollments)
        rows.append(assess(t,p,d,group,course,key,fingerprint,enrollments,evaluation,protected.get(t.pk)))
    state = {**base,'rows':[{'training':r['training_id'],'version':r['version'],'fingerprint':r['fingerprint'],
                           'existing':r['existing'],'changes':r['changes'],'reasons':r['reasons']} for r in rows]}
    return {'key':key,'kind':kind,'group':group,'course':course,'rows':rows,'pending':pending,
            'fingerprint':digest(state),'total_enrolled':len({r['employee_id'] for r in rows}|{r['employee_id'] for r in pending}),
            'completed':sum(r['completion_proved'] for r in rows),
            'eligible':sum(r['eligible'] for r in rows), 'validated':sum(r['existing'] is not None for r in rows),
            'excluded':sum(not r['eligible'] and r['existing'] is None for r in rows)+len(pending),
            'inconsistent':sum(r['inconsistent'] for r in rows)}


def number(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None


def academic(t,p,course,key):
    """No institutional defaults. Accept only an explicitly scoped, approved rule."""
    rule=(course.source_metadata or {}).get('_approved_group_validation_criteria')
    if not isinstance(rule,dict) or not rule.get('approval_reference') or not rule.get('approved_by') or key not in rule.get('groups',[]):
        return 'Por validar',['Critério de aprovação não definido ou não aprovado para este grupo.']
    if rule.get('non_classifying_documented') is True:
        return 'Não aplicável',[]
    minimum=number(rule.get('attendance_min_percent'))
    components=rule.get('components')
    if minimum is None or not 0<=minimum<=100 or not isinstance(components,list) or not components:
        return 'Por validar',['Critério incompleto: são necessários assiduidade e componentes de avaliação.']
    if not p or not p.attendance_complete or p.present_count is None or p.absent_count is None or p.present_count+p.absent_count<=0:
        return 'Pendente de avaliação',['Assiduidade desconhecida ou incompleta.']
    attendance=Decimal(p.present_count)*100/Decimal(p.present_count+p.absent_count)
    missing=[]; failed=[]
    if attendance<minimum:failed.append('Assiduidade insuficiente segundo o critério aprovado.')
    for component in components:
        if not isinstance(component,dict):return 'Por validar',['Componente de avaliação inválida.']
        stage=component.get('stage'); phase=component.get('phase','')
        scale=number(component.get('scale')); limit=number(component.get('minimum'))
        if stage not in ('final','theory','practice') or scale is None or scale<=0 or limit is None or not 0<=limit<=scale:
            return 'Por validar',['Escala ou mínimo da componente não confirmado.']
        grades=[g for g in p.grades if g.get('stage')==stage and g.get('phase','')==phase]
        if not grades:missing.append('Nota obrigatória ausente: '+stage+(' / '+phase if phase else ''));continue
        if len(grades)!=1 or number(grades[0].get('scale'))!=scale:
            return 'Por validar',['Registos contraditórios ou escala incompatível na avaliação.']
        score=number(grades[0].get('value'))
        if score is None or not 0<=score<=scale:return 'Por validar',['Nota fora da escala ou inválida.']
        if score<limit:failed.append('Nota inferior ao mínimo aprovado: '+stage)
    if failed:return 'Reprovado',failed+missing
    if missing:return 'Pendente de avaliação',missing
    return 'Aprovado',[]


def assess(t,p,d,group,course,key,fingerprint,enrollments,evaluation=None,prior=None,include_source_data=True):
    reasons=[]; blocking=[]
    facts=(t.source_data or {}).get('_EPAL_CONSOLIDATED_FACTS') or {}
    old=(t.source_data or {}).get(MARKER)
    if MARKER in (t.source_data or {}) and not old:old={'invalid_previous_decision':True}
    if old is None and evaluation and evaluation.validation=='Validado':
        old={'external':'TrainingEvaluation','pk':evaluation.pk,'fingerprint':fingerprint}
    if old is None and prior:
        old={'external':'AuditEvent','event_ids':prior,'fingerprint':fingerprint}
    if t.course_id!=course.pk or (p and (p.employee_id!=t.employee_id or p.edition.course_id!=t.course_id)):
        blocking.append('Correspondência de formando, formação ou grupo não confirmada.')
    if not t.employee.registry_active:blocking.append('Técnico inactivo no cadastro.')
    if t.status in ('Desistência','Planeado') or getattr(group,'state',None)=='Cancelada':
        blocking.append('Participação excluída: desistência, planeamento ou grupo cancelado.')
    if any(en.employee_id==t.employee_id and en.state!='Activa' for en in enrollments):
        blocking.append('Inscrição cancelada ou transferida.')
    if t.end>timezone.localdate():blocking.append('Formação com data final futura.')
    issues=(p.evidence or {}).get('issues',[]) if p else []
    if issues:blocking.append('Registos contraditórios nas fontes; rever a participação.')
    if p and (p.present_count is not None and p.absent_count is not None) and (p.present_count+p.absent_count<=0):
        blocking.append('Denominador de assiduidade inválido.')
    if p and p.attendance_complete and p.present_count is not None and p.absent_count is not None and p.attendance_percent is not None:
        computed=Decimal(p.present_count)*100/Decimal(p.present_count+p.absent_count) if p.present_count+p.absent_count else None
        if computed is None or abs(computed-p.attendance_percent)>Decimal('.011'):
            blocking.append('Percentagem de assiduidade contraditória.')
    delivered=bool((d and d.learner_delivered is True) or t.certified is True or facts.get('historical_delivery_confirmed') is True)
    delivery_proof=bool((d and d.learner_delivered is True and d.reference.strip()) or
                        (t.certified is True and t.certificate_reference.strip()) or
                        (facts.get('historical_delivery_confirmed') is True and facts.get('participation')))
    if delivered and not delivery_proof:blocking.append('Certificado sem correspondência documental confirmada.')
    shared=(t.source_data or {}).get('_certificate_summary',{}).get('same_certificate_as')
    if shared and not Training.objects.filter(pk=shared,employee_id=t.employee_id,course_id=t.course_id).exists():
        blocking.append('Certificado partilhado sem correspondência confirmada de titular e formação.')
    if delivered and ((d and d.issued is False) or t.certificate_issued is False):
        blocking.append('Estados contraditórios de emissão e entrega; a entrega será preservada.')
    status,academic_reasons=academic(t,p,course,key)
    if delivered and status=='Reprovado':
        status='Por validar'
        academic_reasons.append('Certificado entregue e critério académico em conflito: revisão documental obrigatória, sem reprovação automática.')
    completion=bool(delivery_proof or (p and p.completed is True and p.evidence.get('sources')) or
                    (t.status=='Concluído' and p and p.completed is True and p.attendance_complete and p.evidence))
    # A registered status or last-day attendance alone is never sufficient proof.
    if not completion and status in ('Aprovado','Não aplicável') and p and p.completed is True:completion=True
    if not completion:blocking.append('Conclusão sem evidência suficiente; inscrição ou presença isolada não bastam.')
    if p and (p.present_count==0 and p.attendance_complete) and completion and not delivery_proof:
        blocking.append('Conclusão contraditória com ausência integral documentada.')
    if blocking:status='Por validar'
    if not old:
        reasons=blocking+academic_reasons
        eligible=completion and not blocking
    else:
        eligible=False; reasons=['Decisão anteriormente validada; não será sobrescrita.']
        if not isinstance(old,dict) or old.get('fingerprint')!=fingerprint:
            reasons.append('A evidência mudou após a validação; requer revisão individual.')
    changes={'completion':'Concluído' if completion else 'Por confirmar','academic':status,
             'documentary':'Certificado entregue' if delivered else 'Certificado emitido' if (d and d.issued is True) or t.certificate_issued is True else 'Por confirmar'} if eligible else {}
    inconsistency_prefixes=('Correspondência de','Certificado sem','Certificado partilhado',
                            'Estados contraditórios','Registos contraditórios','Denominador de',
                            'Percentagem de','Conclusão contraditória')
    inconsistent=any(reason.startswith(inconsistency_prefixes) for reason in blocking)
    return {'training_id':t.pk,'employee_id':t.employee_id,'name':t.employee.name,'number':t.employee.number,
            'version':t.version,'fingerprint':fingerprint,'existing':old,'eligible':eligible,
            'completion_proved':completion,'inconsistent':inconsistent or bool(old and (not isinstance(old,dict) or old.get('fingerprint')!=fingerprint)),
            'academic':status,'reasons':reasons,'changes':changes,'source_data':copy.deepcopy(t.source_data) if include_source_data else None,
            'correction_url':reverse('training_edit',args=[t.pk])}


def course_keys(course_id):
    if not str(course_id).isdecimal() or len(str(course_id))>12:
        raise ValidationError('Formação inválida.')
    course=get_object_or_404(Course,pk=course_id)
    native=list(TrainingGroup.objects.filter(course=course).order_by('pk').values_list('pk',flat=True))
    # Same canonical groups as the existing calendar: represented editions are
    # reviewed through their operational group, never twice in one operation.
    represented=HistoricalInterfaceLink.objects.filter(training__group_id__in=native).values_list('participation__edition_id',flat=True)
    editions=list(ConsolidatedEdition.objects.filter(course=course,active=True).exclude(pk__in=represented).order_by('pk').values_list('pk',flat=True))
    return keys_checked([f'group:{pk}' for pk in native]+[f'edition:{pk}' for pk in editions],MAX_COURSE_GROUPS)

def preview(user,keys=None,lock=False,course_id=None):
    authorize(user)
    if course_id is not None:
        resolved=course_keys(course_id)
        if keys is not None and keys_checked(keys,MAX_COURSE_GROUPS)!=resolved:
            raise ValidationError('Os grupos da formação mudaram. Volte a pré-visualizar.')
        keys=resolved
    else:keys=keys_checked(keys)
    groups=[collect(key,lock=lock) for key in keys]
    ids=[r['training_id'] for g in groups for r in g['rows']]
    row_limit=MAX_COURSE_ROWS if course_id is not None else MAX_ROWS
    if len(ids)+sum(len(g['pending']) for g in groups)>row_limit:
        raise ValidationError(f'Seleccione no máximo {row_limit} registos por operação.')
    if len(set(ids))!=len(ids):raise ValidationError('Os grupos seleccionados contêm participações sobrepostas. Valide-os separadamente.')
    payload={'user':user.pk,'keys':keys,'fingerprint':digest([g['fingerprint'] for g in groups])}
    if course_id is not None:payload['course']=int(course_id)
    return {'groups':groups,'token':signing.dumps(payload,salt=SALT,compress=True),
            'eligible':sum(g['eligible'] for g in groups),'fingerprint':payload['fingerprint']}


def apply(user,token,confirmed=False):
    authorize(user)
    if confirmed is not True:raise ValidationError('Confirmação expressa obrigatória.')
    try:payload=signing.loads(token,salt=SALT,max_age=900)
    except (signing.BadSignature,TypeError):raise ValidationError('Pré-visualização inválida ou expirada; volte a rever o grupo.')
    if not isinstance(payload,dict) or payload.get('user')!=user.pk:raise PermissionDenied('Pré-visualização pertence a outro utilizador.')
    try:
        with transaction.atomic():
            current=preview(user,payload.get('keys'),lock=True,course_id=payload.get('course'))
            if current['fingerprint']!=payload.get('fingerprint'):
                raise ValidationError('Os dados mudaram desde a pré-visualização. Nenhuma alteração aplicada; reveja os grupos.')
            changed=[]; timestamp=timezone.now().isoformat()
            for group in current['groups']:
                for row in group['rows']:
                    if not row['eligible']:continue
                    data=copy.deepcopy(row['source_data'])
                    marker={'schema':1,'actor_id':user.pk,'actor':user.get_username(),'timestamp':timestamp,
                            'group':group['key'],'fingerprint':row['fingerprint'],'decisions':row['changes'],
                            'reasons':row['reasons']}
                    data[MARKER]=marker
                    # CAS guards SQLite (no row locks) and other writers that omit version increments.
                    updated=Training.objects.filter(pk=row['training_id'],version=row['version'],source_data=row['source_data']).update(source_data=data,version=F('version')+1)
                    if updated!=1:raise ValidationError('Alteração concorrente detectada; operação integralmente anulada.')
                    event=AuditEvent.objects.create(actor=user.get_username(),action=ACTION,detail=canonical({
                        'group':group['key'],'training_id':row['training_id'],'employee_id':row['employee_id'],
                        'actor_id':user.pk,'timestamp':timestamp,'fingerprint':row['fingerprint'],
                        'before':{'validation':None,'version':row['version']},
                        'after':{'validation':marker,'version':row['version']+1}}))
                    changed.append({'training_id':row['training_id'],'audit_event_id':event.pk})
            return changed
    except OperationalError as exc:
        if any(word in str(exc).lower() for word in ('locked','busy','deadlock','serialize')):
            raise ValidationError('Outra operação está em curso. Nenhuma alteração aplicada; volte a pré-visualizar.') from exc
        raise

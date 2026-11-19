"""Single statistical and write service for the official formation questionnaire."""
from copy import deepcopy
import json
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import IntegrityError
from django.db.models import Q
from .formation_questionnaire import digest,question_metrics

def prototype_context(request):
    from django.conf import settings
    return {'formation_survey_prototype':getattr(settings,'PMF_FORMATION_SURVEY_PROTOTYPE',False)}

def require(user,permission):
    if not user.is_active or not user.is_staff or not user.has_perms(['training.view_training','training.view_traininggroup','training.'+permission]):raise PermissionDenied

def group_people(group):
    from .models import Employee,Enrollment
    from .group_roster import group_trainings
    ids=Enrollment.objects.filter(group=group,state='Activa').exclude(confirmation='Não participa').values_list('employee_id',flat=True)
    historical=group_trainings(group).values_list('employee_id',flat=True)
    return Employee.objects.filter(Q(pk__in=ids)|Q(pk__in=historical)).distinct().order_by('name','number')

def validate_answers(schema,answers):
    questions={q['key']:q for q in schema['questions']}
    if not isinstance(answers,dict) or set(answers)-set(questions):raise ValidationError('Pergunta desconhecida nesta versão.')
    for key,value in answers.items():
        allowed=[1,5] if questions[key]['kind']=='yesno' else [1,2,3,4,5]
        if value is not None and (type(value) is not int or value not in allowed):raise ValidationError('Classificação inválida; recomendação permite apenas Não=1 e Sim=5.')

def validate_counts(schema,counts,received,state):
    questions={q['key']:q for q in schema['questions']}
    if type(received) is not int or received<0 or received>100000:raise ValidationError('Número de fichas inválido.')
    if not isinstance(counts,dict) or set(counts)-set(questions):raise ValidationError('Pergunta desconhecida nas contagens.')
    for key,values in counts.items():
        if not isinstance(values,list) or len(values)!=5 or any(v is not None and (type(v) is not int or v<0 or v>received) for v in values):raise ValidationError('As contagens devem ser inteiros não negativos, limitados às fichas recebidas.')
        if questions[key]['kind']=='yesno' and any(v not in [0,None] for v in values[1:4]):raise ValidationError('Não usar classificações 2, 3 e 4 na recomendação.')
        endpoints=[values[0],values[4]] if questions[key]['kind']=='yesno' else values
        if any(v is not None for v in endpoints) and any(v is None for v in endpoints) and state=='completed':raise ValidationError('Complete a linha de contagens ou deixe-a inteiramente sem resposta.')
        if sum(v or 0 for v in values)>received:raise ValidationError('As respostas de uma pergunta excedem as fichas recebidas.')

def audit(user,action,obj,before,reason=''):
    from .models import AuditEvent
    # No employee, respondent account, IP, or free-text contents in anonymous audit.
    after=dict(id=obj.pk,survey=obj.survey_id,state=obj.state,revision=obj.revision,
        data=obj.answers if hasattr(obj,'answers') else obj.counts,
        improvements_digest=digest(obj.improvements),future_training_digest=digest(obj.future_training))
    AuditEvent.objects.create(actor=user.get_username(),action=action,detail=json.dumps(dict(before=before,after=after,reason=reason),ensure_ascii=False,default=str))

def create_survey(user,group,questionnaire,mode,anonymous,evaluation_date):
    require(user,'add_formationsurvey')
    from .models import FormationSurvey,AuditEvent
    from .scheduling_guard import reservation_transaction
    with reservation_transaction():
        if FormationSurvey.objects.filter(group=group).exists():raise ValidationError('Este grupo já tem uma avaliação. Abra o conjunto existente.')
        survey=FormationSurvey(group=group,questionnaire=questionnaire,questionnaire_digest=questionnaire.schema_digest,mode=mode,anonymous=anonymous,evaluation_date=evaluation_date)
        survey.full_clean();survey.save()
        AuditEvent.objects.create(actor=user.get_username(),action='FORMATION_SURVEY_CREATED',detail=json.dumps(dict(survey=survey.pk,group=group.pk,mode=mode,anonymous=anonymous,questionnaire_digest=survey.questionnaire_digest)))
        return survey

def save_entry(user,survey_id,values,pk=None,revision=0,reason=''):
    from .models import FormationSurvey,FormationSurveyResponse,FormationSurveyAggregate
    from .scheduling_guard import reservation_transaction
    with reservation_transaction():
        survey=FormationSurvey.objects.select_related('group','questionnaire').get(pk=survey_id);survey.clean()
        model=FormationSurveyResponse if survey.mode=='individual' else FormationSurveyAggregate
        permission=('change_' if pk else 'add_')+model._meta.model_name;require(user,permission)
        if values.get('state')=='draft' and not user.has_perm('training.save_partial_survey'):raise PermissionDenied('Guardar rascunhos exige permissão específica.')
        old=model.objects.filter(pk=pk,survey=survey).first() if pk else None
        if pk and not old:raise ValidationError('Ficha não pertence a este grupo.')
        if old and old.revision!=revision:raise ValidationError('A ficha mudou noutra janela. Reabra antes de corrigir.')
        if old and not reason.strip():raise ValidationError('Indique o motivo da correcção.')
        obj=old or model(survey=survey)
        before=dict(revision=old.revision,state=old.state,data=deepcopy(old.answers if survey.mode=='individual' else old.counts)) if old else None
        allowed={'reference','employee_id','answers','improvements','future_training','state'} if survey.mode=='individual' else {'received_forms','counts','improvements','future_training','state','reason'}
        if set(values)-allowed:raise ValidationError('Campos não autorizados.')
        for key,value in values.items():setattr(obj,key,value)
        if survey.mode=='individual':
            obj.reference=obj.reference.strip().upper()
            if not obj.reference:raise ValidationError('Indique o código único da ficha em papel ou digital.')
            if not survey.anonymous:require(user,'view_employee')
        obj.revision=(old.revision+1) if old else 1;obj.full_clean()
        try:obj.save()
        except IntegrityError as exc:raise ValidationError('Ficha repetida ou já registada para este participante.') from exc
        audit(user,'FORMATION_SURVEY_ENTRY_SAVED',obj,before,reason)
        return obj

def configure_survey(user,pk,values,revision,reason):
    require(user,'change_formationsurvey')
    from .models import FormationSurvey,AuditEvent
    from .scheduling_guard import reservation_transaction
    with reservation_transaction():
        survey=FormationSurvey.objects.select_related('questionnaire').get(pk=pk)
        if revision!=survey.revision:raise ValidationError('A configuração mudou noutra janela. Reabra a avaliação.')
        if not reason.strip():raise ValidationError('Indique o motivo da correcção.')
        before={k:str(getattr(survey,k)) for k in ['mode','anonymous','evaluation_date','questionnaire_digest']}
        for key in ['mode','anonymous','evaluation_date','questionnaire']:
            if key in values:setattr(survey,key,values[key])
        survey.questionnaire_digest=survey.questionnaire.schema_digest;survey.full_clean();survey.revision+=1;survey.save()
        AuditEvent.objects.create(actor=user.get_username(),action='FORMATION_SURVEY_CONFIGURED',detail=json.dumps(dict(survey=pk,before=before,after={k:str(getattr(survey,k)) for k in before},reason=reason)))
        return survey

def summary(surveys,employee_ids=None):
    surveys=list(surveys);questions=None;counts={};received=0;drafts=0;participants=0;groups=[];comments=[];excluded=0;warnings=[]
    version=None
    for survey in surveys:
        survey.clean()
        if version is not None and version!=survey.questionnaire_digest:raise ValidationError('Seleccione uma única versão de questionário para consolidar resultados.')
        version=survey.questionnaire_digest;schema=survey.questionnaire.schema
        if questions is None:questions=schema;counts={q['key']:[0]*5 for q in schema['questions']}
        if employee_ids is not None and (survey.anonymous or survey.mode=='aggregate'):excluded+=1;continue
        participants+=group_people(survey.group).filter(pk__in=employee_ids).count() if employee_ids is not None else group_people(survey.group).count()
        group_received=0
        if survey.mode=='individual':
            rows=survey.responses.all()
            if employee_ids is not None:rows=rows.filter(employee_id__in=employee_ids)
            for row in rows:
                validate_answers(schema,row.answers)
                if row.state!='completed':drafts+=1;continue
                received+=1;group_received+=1
                for key,value in row.answers.items():
                    if value is not None:counts[key][value-1]+=1
                for field in ['improvements','future_training']:
                    text=getattr(row,field).strip()
                    if text:comments.append(dict(kind=field,text=text))
        else:
            aggregate=getattr(survey,'aggregate',None)
            if aggregate and aggregate.state=='completed':
                validate_counts(schema,aggregate.counts,aggregate.received_forms,aggregate.state)
                received+=aggregate.received_forms;group_received=aggregate.received_forms
                for q in schema['questions']:
                    values=aggregate.counts.get(q['key'],[None]*5)
                    relevant=[values[0],values[4]] if q['kind']=='yesno' else values
                    if all(v is not None for v in relevant):
                        for i,v in enumerate(values):counts[q['key']][i]+=v or 0
                for field in ['improvements','future_training']:
                    text=getattr(aggregate,field).strip()
                    if text:comments.append(dict(kind=field,text=text))
            elif aggregate:drafts+=1
        groups.append(dict(id=survey.group_id,code=survey.group.code,title=survey.group.title,received=group_received,mode=survey.mode))
        if group_received>group_people(survey.group).count():warnings.append(f'{survey.group.code}: {group_received} fichas para {group_people(survey.group).count()} participantes registados; conferir a origem da diferença.')
    from .formation_questionnaire import SCHEMA
    question_rows,dimensions=question_metrics(questions or SCHEMA,counts)
    recommendation=next((q for q in question_rows if q['kind']=='yesno'),None)
    return dict(questions=question_rows,dimensions=dimensions,received=received,drafts=drafts,participants=participants,groups=groups,
        valid_responses=sum(q['n'] for q in question_rows),recommendation=recommendation,comments=comments,excluded_anonymous_groups=excluded,warnings=warnings)

def for_trainings(rows,nominal=False):
    from .models import FormationSurvey
    rows=list(rows);groups={r.group_id for r in rows if r.group_id}
    surveys=FormationSurvey.objects.filter(group_id__in=groups,questionnaire__code='proagua-formation-v1').select_related('group','questionnaire').prefetch_related('responses','aggregate')
    # An anonymous response cannot be apportioned to an employee/direction.
    return summary(surveys,{r.employee_id for r in rows} if nominal else None)

def attach_context(request,data):
    if request.user.is_staff and request.user.has_perms(['training.view_formationsurvey','training.view_traininggroup','training.view_training']):
        from .models import FormationSurvey
        selected=data.get('selected',{})
        nominal=any(selected.get(k) for k in ['direction','gender','category','status'])
        surveys=FormationSurvey.objects.filter(questionnaire__code='proagua-formation-v1').select_related('group','questionnaire').prefetch_related('responses','aggregate')
        if selected.get('area'):surveys=surveys.filter(group__area=selected['area'])
        if data.get('date_from'):surveys=surveys.filter(group__start__gte=data['date_from'])
        if data.get('date_to'):surveys=surveys.filter(group__start__lte=data['date_to'])
        if nominal:surveys=surveys.filter(group_id__in={r.group_id for r in data['rows'] if r.group_id})
        data['formation_evaluation']=summary(surveys,{r.employee_id for r in data['rows']} if nominal else None)
        data['formation_evaluation_export_allowed']=request.user.has_perm('training.export_formation_evaluation')
    return data

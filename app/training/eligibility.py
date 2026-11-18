"""One eligibility policy for selection, enrolment, publication and XLSX export."""
import hashlib
import json
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import TextField
from django.db.models.functions import Cast
from django.urls import reverse
from .models import Training, Enrollment, EligibilityDecision


def enabled():
    return getattr(settings,'PMF_SIMPLIFIED_PLANNING_ENABLED',False)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,ensure_ascii=True).encode()).hexdigest()


def target_calendar(group,draft):
    from .planning import GroupForm
    if group:
        values={k:getattr(group,k) for k in ('trainer','provider','location','capacity','hours_per_day','break_hours','start_time','end_time')}
        values['dates']=list(group.sessions.values_list('date',flat=True))
    elif draft:
        form=GroupForm(draft.group_data)
        if not form.is_valid():return {'incomplete':draft.group_data}
        values=form.cleaned_data.copy()
    else:return None
    return {k:[d.isoformat() for d in v] if k=='dates' else str(v.normalize()) if isinstance(v,Decimal)
            else v.isoformat() if hasattr(v,'isoformat') else v for k,v in values.items() if k in ('dates','trainer','provider','location','capacity','hours_per_day','break_hours','start_time','end_time')}


def assess(course, people, *, group=None, draft=None):
    from .planning import normalise
    from . import historical
    people=list(people);ids=[p.pk for p in people]
    calendar=target_calendar(group,draft)
    manifest=historical.manifest() if historical.enabled() else {'bindings':{},'editions':{}}
    previous=list(Training.objects.filter(employee_id__in=ids).select_related('employee','group__course')
                  .annotate(source_json_text=Cast('source_data',TextField())).order_by('pk'))
    active=list(Enrollment.objects.filter(employee_id__in=ids,state='Activa',
        group__state__in=['Planeada','Em Curso'],group__course=course).exclude(group=group)
        .select_related('group').order_by('pk')) if course.pk else []
    existing=set(group.enrollments.filter(employee_id__in=ids).values_list('employee_id',flat=True)) if group else set()
    titles={normalise(course.title),*(normalise(a) for a in course.aliases)}
    entries={p.pk:{'person':p,'warnings':[],'other':[],'blocked':p.pk in existing,'fingerprint':[],
                     'proven':False,'uncertain':False,'absent':False} for p in people}
    for row in previous:
        if group and row.group_id==group.pk:
            continue  # this group's own attendance is not a previous participation
        binding=manifest['bindings'].get(row.source_key)
        bound=bool(binding and binding.get('level')=='confirmed' and course.pk and
            course.source_system=='epal-proagua-catalogo' and binding.get('action_id')==course.source_action_id and
            historical.signature(row)==binding.get('signature') and
            hashlib.sha256(str(row.employee.number).strip().encode()).hexdigest()==binding.get('employee_number_sha256'))
        if row.course_id and row.course_id!=course.pk:continue
        if row.group_id:
            if row.group.course_id!=course.pk:continue
            identified=True
        else:
            candidate_binding=bool(binding and course.source_action_id and binding.get('action_id')==course.source_action_id)
            if not bound and row.course_id!=course.pk and normalise(row.title) not in titles and not candidate_binding:continue
            # An exact text match raises a question; it never establishes identity.
            identified=bound or bool(course.pk and row.course_id==course.pk)
        item=entries[row.employee_id]
        label=f'{row.group.code if row.group_id else "Histórico"} · {row.start:%d/%m/%Y} · {row.status}'
        if bound:
            edition=manifest['editions'].get(binding.get('edition'),{})
            label=f'{edition.get("display_id","Histórico")} · {row.start:%d/%m/%Y} · {row.status}'
        proven=identified and row.status=='Concluído' and row.attendance_confirmed and (row.attendance or 0)>0
        absent=identified and row.status in ('Concluído','Desistência') and row.attendance_confirmed and row.attendance==0
        kind='Participação anterior comprovada' if proven else 'Ausência confirmada' if absent else 'Participação por verificar'
        item['warnings'].append({'kind':kind,'note':'Este formando já participou nesta formação. Consulte o histórico antes de efectuar uma nova inscrição. Repetição exige autorização específica.' if proven else
            'Nova inscrição permitida após ausência validada.' if absent else 'É necessária análise; não se presume conclusão nem ausência.', 'label':label})
        item['proven'] |= proven;item['absent'] |= absent;item['uncertain'] |= not(proven or absent)
        item['fingerprint'].append(('t',row.pk,row.version,historical.signature(row),row.status,row.attendance,
            row.attendance_confirmed,row.grade,row.certified,identified,binding if bound else None))
    for en in active:
        item=entries[en.employee_id];item['other'].append(en)
        item['warnings'].append({'kind':'Inscrição activa noutro grupo','note':'Não é permitida uma segunda inscrição activa.',
            'label':f'{en.group.code} · {en.group.start:%d/%m/%Y} · {en.group.state}'})
        item['fingerprint'].append(('e',en.pk,en.group_id,en.state,en.group.revision,en.training_id))
    decisions=EligibilityDecision.objects.filter(employee_id__in=ids)
    decisions=decisions.filter(group=group) if group else decisions.filter(draft=draft,group__isnull=True) if draft else decisions.none()
    decisions=list(decisions.order_by('-pk'))
    result=[]
    for item in entries.values():
        item['can_transfer']=bool(item['other']) and all(en.training_id is None and en.group.state=='Planeada' for en in item['other'])
        item['state']='conflict' if item['other'] else 'repeat' if item['proven'] else 'review' if item['uncertain'] else 'absent' if item['absent'] else 'eligible'
        item['decision_kind']='repeat' if item['proven'] else 'review' if item['uncertain'] else None
        item['basis']=digest([course.pk,course.title,course.aliases,course.revision,course.validation_status,
                              course.active,calendar,item['person'].pk,item['fingerprint']])
        item['authorization']=next((d for d in decisions if d.employee_id==item['person'].pk and
            d.course_id==course.pk and d.basis==item['basis'] and d.kind==item['decision_kind']),None)
        item['needs_reason']=bool(item['other'] or item['state'] in ('repeat','review') and not item['authorization'])
        item['key']=digest([item['basis'],item['blocked'],item['authorization'].pk if item['authorization'] else None])
        if item['decision_kind'] and not item['authorization'] and (group or draft):
            item['authorization_url']=reverse('eligibility_group_decision',args=[group.pk,item['person'].pk]) if group else reverse('eligibility_draft_decision',args=[draft.pk,item['person'].pk])
        result.append(item)
    return result


def require_admission(item, choice='add'):
    name=item['person'].name
    if item['blocked']:raise ValidationError('Este formando já se encontra inscrito nesta edição da formação. Não é possível efectuar uma inscrição duplicada.')
    if item['other'] and not(choice=='transfer' and item['can_transfer']):
        raise ValidationError(f'{name}: já inscrito noutro grupo activo. Transfira a inscrição quando permitido.')
    if item['decision_kind'] and not item['authorization']:
        raise ValidationError(f'{name}: {"participação anterior comprovada" if item["proven"] else "participação por verificar"}; falta decisão administrativa específica.')


def validate_export(group, enrollments):
    if not group.course.can_create_groups:
        raise ValidationError('A formação deve estar disponível para a lista operacional.')
    entries=assess(group.course,[e.employee for e in enrollments],group=group)
    errors=[]
    for item in entries:
        item['blocked']=False  # exporting the existing enrolment, not adding it twice
        try:require_admission(item)
        except ValidationError as exc:errors.extend(exc.messages)
    if errors:raise ValidationError(['Lista não gerada. Reveja os bloqueios:']+errors)

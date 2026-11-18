from .training_areas import AREA_CHOICES
"""Persistent preparation and atomic publication through existing group services."""
import json
from datetime import date, timedelta
from copy import copy
from types import SimpleNamespace
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from .models import PlanningDraft, DraftParticipant, Course, EligibilityDecision, AuditEvent
from .planning import GroupForm, create_group, enrol_reviewed, normalise
from .eligibility import assess, require_admission, digest, enabled


def require(user, permission):
    if not enabled() or not(user.is_authenticated and user.is_active and user.is_staff and user.has_perm('training.'+permission)):
        raise PermissionDenied


def audit(user, action, **detail):
    AuditEvent.objects.create(actor=user.get_username(),action=action,detail=json.dumps(detail,ensure_ascii=False,default=str))


class DraftForm(GroupForm):
    change_reason=forms.CharField(label='Motivo da correcção (rascunho)',max_length=1000,required=False,help_text='Indique o motivo quando estiver a corrigir uma preparação já guardada.')
    start_date=forms.DateField(label='Data de início',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    end_date=forms.DateField(label='Data de fim',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    calendar_mode=forms.ChoiceField(label='Calendário',required=False,choices=[('range','Dias consecutivos'),('custom','Dias não consecutivos / editar calendário')])
    course=forms.ModelChoiceField(label='Formação existente',queryset=Course.objects.all(),required=False)
    title=forms.CharField(label='Nome da nova formação',max_length=1000,required=False)
    area=forms.ChoiceField(label='Área',choices=[('', 'Por confirmar'),*AREA_CHOICES],required=False)

    def __init__(self,*args,mode='existing',**kwargs):
        self.mode=mode
        super().__init__(*args,**kwargs)
        for field in self.fields.values():field.required=False
        self.fields['revision'].required=True
        if mode=='existing':
            self.fields['course'].required=True
            self.fields.pop('title');self.fields.pop('area')
        else:
            self.fields.pop('course');self.fields['title'].required=True
            self.fields['area'].required=False
        self.fields['dates'].widget=forms.Textarea(attrs={'rows':4})
        self.fields['dates'].label='Dias da turma'
        self.fields['dates'].help_text='Uma data por linha. Pode guardar o rascunho antes de completar o calendário.'
        if not self.is_bound:
            try:
                days=sorted(date.fromisoformat(x.strip()) for x in self.initial.get('dates','').replace(',','\n').splitlines() if x.strip())
            except ValueError:days=[]
            if days:
                self.initial.update(start_date=days[0],end_date=days[-1],calendar_mode='range' if (days[-1]-days[0]).days+1==len(days) else 'custom')
            else:self.initial.setdefault('calendar_mode','range')

    def clean_dates(self):
        if self.data.get('calendar_mode')=='range':
            start=self.fields['start_date'].clean(self.data.get('start_date'))
            end=self.fields['end_date'].clean(self.data.get('end_date'))
            if start is None and end is None:return []
            if start is None or end is None:raise ValidationError('Indique as datas de início e fim, ou deixe ambas por preencher.')
            count=(end-start).days+1
            if not 1<=count<=366:raise ValidationError('O fim deve ser igual ou posterior ao início, com 1 a 366 dias efectivos.')
            return [start+timedelta(days=i) for i in range(count)]
        return super().clean_dates() if self.cleaned_data.get('dates') else []

    def clean_title(self):
        title=self.cleaned_data.get('title','').strip()
        if any(normalise(title) in {normalise(c.title),*(normalise(a) for a in c.aliases)} for c in Course.objects.all()):
            raise ValidationError('Esta formação já existe. Escolha Formação existente.')
        return title


GROUP_FIELDS=('dates','trainer','provider','location','capacity','hours_per_day','break_hours','start_time','end_time')


def serialize(data):
    result={}
    for key in GROUP_FIELDS:
        value=data.get(key)
        result[key]='\n'.join(d.isoformat() for d in value) if key=='dates' else str(value) if value is not None else ''
    return result


def draft_course(draft):
    return draft.course if draft.course_id else Course(title=draft.title,area=draft.area,default_days=None,default_hours=None)


def entries(draft):
    members=list(draft.participants.select_related('employee'))
    checked=assess(draft_course(draft),[m.employee for m in members],draft=draft)
    by_id={m.employee_id:m for m in members}
    for item in checked:
        item['member']=by_id[item['person'].pk]
    return checked


def validate(draft, *, preparation=False):
    if draft.state!='draft':raise ValidationError('O rascunho já foi publicado; abra a turma criada.')
    if not draft.group_data.get('dates'):raise ValidationError('Indique pelo menos um dia no calendário da turma.')
    form=GroupForm(draft.group_data)
    if not form.is_valid():
        raise ValidationError('Complete os dados da turma: '+'; '.join(
            (form.fields[key].label if key in form.fields else 'Dados da turma')+': '+' '.join(errors)
            for key,errors in form.errors.items()))
    if not preparation and (not draft.course_id or not draft.course.can_create_groups):
        raise ValidationError('Seleccione uma formação disponível antes de publicar o grupo.')
    checked=entries(draft)
    if not checked:raise ValidationError('Adicione pelo menos um formando ao rascunho.')
    if len(checked)>form.cleaned_data['capacity']:raise ValidationError('A selecção ultrapassa a capacidade da turma.')
    errors=[]
    for item in checked:
        choice='transfer' if item['member'].transfer_requested and not preparation else 'add'
        try:require_admission(item,choice)
        except ValidationError as exc:errors.extend(exc.messages)
    if errors:raise ValidationError(errors)
    return form.cleaned_data,checked


@transaction.atomic
def save(user,form,draft=None):
    require(user,'change_planningdraft' if draft else 'add_planningdraft')
    data=form.cleaned_data
    course=data.get('course')
    before={'course':draft.course_id,'title':draft.title,'area':draft.area,'group_data':draft.group_data,'revision':draft.revision} if draft else None
    values={'course':course,'title':course.title if course else data['title'],
            'area':course.area if course else data.get('area',''),'group_data':serialize(data)}
    if draft:
        if PlanningDraft.objects.filter(pk=draft.pk,state='draft',revision=data['revision']).update(revision=F('revision')+1,**values)!=1:
            raise ValidationError('Rascunho alterado noutra janela. Reabra antes de guardar.')
        draft.refresh_from_db()
    else:
        if data['revision']!=0:raise ValidationError('Revisão inválida para um novo rascunho.')
        draft=PlanningDraft.objects.create(owner=user,**values)
    review=preparation_review(draft)
    audit(user,'PLANNING_DRAFT_SAVE',draft=draft.pk,revision=draft.revision,values=values,
          before=before,after=values,reason=data.get('change_reason',''),eligibility=review)
    return draft


def preparation_review(draft):
    """Reassess preparation without treating a selection as an enrolment."""
    result=[]
    for item in entries(draft):
        choice='transfer' if item['member'].transfer_requested else 'add'
        try:
            require_admission(item,choice)
            eligible=True
        except ValidationError:
            eligible=False
        result.append({'employee':item['person'].pk,'eligible':eligible,'basis':item['basis'],
                       'proven':item['proven'],'uncertain':item['uncertain']})
    return result


def lock(draft,revision):
    if PlanningDraft.objects.filter(pk=draft.pk,state='draft',revision=revision).update(revision=F('revision')+1)!=1:
        raise ValidationError('O rascunho mudou ou já foi publicado. Reabra a página.')
    draft.refresh_from_db()


@transaction.atomic
def register_course(user,draft,revision):
    require(user,'change_planningdraft');require(user,'add_course')
    draft=copy(draft)
    lock(draft,revision)
    if draft.course_id:raise ValidationError('A acção já está registada; não será criada novamente.')
    if any(normalise(draft.title) in {normalise(c.title),*(normalise(a) for a in c.aliases)} for c in Course.objects.all()):
        raise ValidationError('A acção já existe. Associe o rascunho à formação existente.')
    course=Course(title=draft.title,area=draft.area,default_days=None,default_hours=None,validation_status='pending')
    course.full_clean();course.save()
    draft.course=course;draft.save(update_fields=['course','updated'])
    audit(user,'DRAFT_COURSE_PENDING',draft=draft.pk,course=course.pk,validation='pending')
    return course


@transaction.atomic
def publish(user,draft,revision,expected):
    for permission in ('change_planningdraft','publish_planningdraft','add_traininggroup','add_enrollment'):require(user,permission)
    draft=copy(draft)
    lock(draft,revision)
    if draft.course_id:
        Course.objects.filter(pk=draft.course_id).update(next_group=F('next_group'))
        draft.course.refresh_from_db()
    data,checked=validate(draft)
    keys={str(x['person'].pk):x['key'] for x in checked}
    if keys!=expected:raise ValidationError('Histórico ou autorizações alterados desde a revisão. Reveja a selecção.')
    group=create_group(draft.course,data)
    # Specific draft decisions remain preserved, now associated with the published group.
    EligibilityDecision.objects.filter(draft=draft,group__isnull=True).update(group=group)
    current=assess(group.course,[x['person'] for x in checked],group=group)
    current_keys={str(x['person'].pk):x['key'] for x in current}
    if current_keys!=keys:raise ValidationError('A elegibilidade mudou durante a publicação.')
    decisions={str(x['person'].pk):('transfer' if x['member'].transfer_requested else 'add',x['member'].reason) for x in checked}
    enrol_reviewed(group,[x['person'] for x in checked],decisions,current_keys,group.revision,actor=user)
    draft.state='published';draft.published_group=group
    draft.save(update_fields=['state','published_group','updated'])
    audit(user,'PLANNING_DRAFT_PUBLISH',draft=draft.pk,group=group.pk,number=group.number,employees=list(keys))
    return group


def preparation_excel(draft,day):
    from .attendance_excel import build_attendance_excel
    data,checked=validate(draft,preparation=True)
    if day not in data['dates']:raise ValidationError('Seleccione um dia do calendário do rascunho.')
    # Mark every printed page in the existing title cell; no source XLSX change.
    group=SimpleNamespace(**{k:v for k,v in data.items() if k!='dates'},
        title='PREPARAÇÃO — RASCUNHO (SEM VALOR DE COMPROVATIVO) · '+draft.title,code='SEM CÓDIGO DEFINITIVO')
    return build_attendance_excel(group,day,[SimpleNamespace(employee=x['person']) for x in checked])

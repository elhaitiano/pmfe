from .professional_ui import person_ids
"""Gated UI for preparation only; no historical Training rows are created here."""
import io
from datetime import date
from functools import wraps
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError, OperationalError
from django.db.models import F, Q
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .models import PlanningDraft, DraftParticipant, EligibilityDecision, TrainingGroup, Employee, Course
from . import draft_planning as service
from .eligibility import enabled, assess, digest, require_admission

SALT='planning-draft-publication-v1'
DECISION_SALT='planning-eligibility-v1'


def access(permission):
    def decorator(view):
        @login_required
        @wraps(view)
        def guarded(request,*args,**kwargs):
            if not enabled():raise Http404
            service.require(request.user,permission)
            service.require(request.user,'view_employee')
            service.require(request.user,'view_training')
            return view(request,*args,**kwargs)
        return guarded
    return decorator


@access('view_planningdraft')
@require_http_methods(['GET'])
def index(request):
    drafts=PlanningDraft.objects.select_related('course','owner','published_group')
    return render(request,'planning/drafts.html',{'drafts':Paginator(drafts,20).get_page(request.GET.get('page'))})


@access('add_planningdraft')
@require_http_methods(['GET','POST'])
def create(request):
    mode=request.GET.get('mode','existing')
    if mode not in ('existing','new'):raise Http404
    form=service.DraftForm(request.POST if request.method=='POST' else None,mode=mode,
        initial={'revision':0,'capacity':30,'break_hours':0,'start_time':'08:00','end_time':'16:00'})
    if request.method=='POST' and form.is_valid():
        try:
            draft=service.save(request.user,form)
            messages.success(request,'Rascunho guardado. Pode adicionar formandos.')
            return redirect('planning_draft_edit',pk=draft.pk)
        except (ValidationError,IntegrityError) as exc:form.add_error(None,str(exc))
    return render(request,'planning/draft_form.html',{'form':form,'mode':mode,'course_numbers':{str(pk):number for pk,number in Course.objects.values_list('pk','next_group')}})


@access('change_planningdraft')
@require_http_methods(['GET','POST'])
def edit(request,pk):
    draft=get_object_or_404(PlanningDraft.objects.select_related('course','published_group'),pk=pk)
    if draft.state=='published':return redirect('group_detail',pk=draft.published_group_id)
    mode='existing' if draft.course_id else 'new'
    form=service.DraftForm(request.POST if request.method=='POST' and request.POST.get('action')=='save' else None,
        mode=mode,initial={**draft.group_data,'revision':draft.revision,'course':draft.course_id,'title':draft.title,'area':draft.area})
    preview=None;token=''
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='save' and form.is_valid():
                draft=service.save(request.user,form,draft)
                messages.success(request,'Dados do grupo guardados.')
                return redirect('planning_draft_edit',pk=draft.pk)
            elif action=='register':
                if request.POST.get('confirm')!='yes':raise ValidationError('Confirme o registo da acção pendente.')
                service.register_course(request.user,draft,int(request.POST.get('revision','0')))
                messages.success(request,'Acção registada como pendente. Nenhuma aprovação foi atribuída.')
                return redirect('planning_draft_edit',pk=draft.pk)
            elif action=='review':
                for perm in ('publish_planningdraft','add_traininggroup','add_enrollment'):service.require(request.user,perm)
                _,preview=service.validate(draft)
                token=signing.dumps({'user':request.user.pk,'draft':str(draft.pk),'revision':draft.revision,
                    'keys':{str(x['person'].pk):x['key'] for x in preview}},salt=SALT)
            elif action=='publish':
                if request.POST.get('confirm')!='yes':raise ValidationError('Confirme expressamente a criação da turma.')
                payload=signing.loads(request.POST.get('token',''),salt=SALT,max_age=900)
                if payload['user']!=request.user.pk or payload['draft']!=str(draft.pk):raise ValidationError('Proposta de outro utilizador ou rascunho.')
                group=service.publish(request.user,draft,payload['revision'],payload['keys'])
                return redirect('group_detail',pk=group.pk)
        except (ValidationError,signing.BadSignature,ValueError,KeyError,IntegrityError,OperationalError) as exc:
            messages.error(request,'Operação concorrente. Reabra o rascunho.' if isinstance(exc,(IntegrityError,OperationalError)) else str(exc))
    return render(request,'planning/draft_form.html',{'draft':draft,'mode':mode,'form':form,'entries':service.entries(draft),
        'preview':preview,'token':token,'dates':draft.group_data.get('dates','').splitlines(),
        'calendar_days':[date.fromisoformat(d) for d in draft.group_data.get('dates','').splitlines()],
        'course_numbers':{str(pk):number for pk,number in Course.objects.values_list('pk','next_group')}})


@access('change_planningdraft')
@require_http_methods(['GET','POST'])
def participants(request,pk):
    draft=get_object_or_404(PlanningDraft.objects.select_related('course'),pk=pk,state='draft')
    service.require(request.user,'view_employee')
    if request.method=='POST':
        try:
            with transaction.atomic():
                service.lock(draft,int(request.POST.get('revision','0')))
                action=request.POST.get('action')
                before=list(draft.participants.values_list('employee_id',flat=True))
                reason=request.POST.get('reason','').strip()
                if len(reason)>1000:raise ValidationError('Indique um motivo com até 1.000 caracteres.')
                if action=='add':
                    service.require(request.user,'add_draftparticipant')
                    raw=request.POST.getlist('employees')
                    if not raw or len(raw)>1000 or any(not x.isdecimal() or len(x)>18 for x in raw):raise ValidationError('Seleccione formandos válidos.')
                    people=list(Employee.objects.filter(pk__in={int(x) for x in raw}))
                    if len(people)!=len(set(raw)):raise ValidationError('Formando não encontrado; refaça a selecção.')
                    for person in people:DraftParticipant.objects.get_or_create(draft=draft,employee=person)
                elif action in ('remove','transfer'):
                    service.require(request.user,'delete_draftparticipant' if action=='remove' else 'change_draftparticipant')
                    member=get_object_or_404(DraftParticipant,draft=draft,pk=request.POST.get('member'))
                    if action=='remove':member.delete()
                    else:
                        reason=request.POST.get('reason','').strip()
                        if not 3<=len(reason)<=1000:raise ValidationError('Indique o motivo da transferência, até 1.000 caracteres.')
                        item=assess(service.draft_course(draft),[member.employee],draft=draft)[0]
                        if not item['can_transfer']:raise ValidationError('A inscrição de origem não permite transferência.')
                        member.transfer_requested=True;member.reason=reason;member.save(update_fields=['transfer_requested','reason'])
                else:raise ValidationError('Operação desconhecida.')
                review=service.preparation_review(draft)
                service.audit(request.user,'DRAFT_PARTICIPANTS',draft=draft.pk,operation=action,revision=draft.revision,
                    before=before,after=list(draft.participants.values_list('employee_id',flat=True)),reason=reason,eligibility=review)
            messages.success(request,'Formandos adicionados à preparação.' if action=='add' else 'Selecção guardada.')
            return redirect('planning_draft_people',pk=draft.pk)
        except (ValidationError,ValueError,IntegrityError,OperationalError) as exc:messages.error(request,str(exc))
    q=request.GET.get('q','').strip()
    people=Employee.objects.all()
    if q:people=people.filter(Q(pk__in=person_ids(q)))
    page=Paginator(people,20).get_page(request.GET.get('page'))
    checked=assess(service.draft_course(draft),list(page),draft=draft)
    selected=set(draft.participants.values_list('employee_id',flat=True))
    for item in checked:item['selected']=item['person'].pk in selected
    return render(request,'planning/draft_people.html',{'draft':draft,'page':page,'q':q,'candidates':checked,'entries':service.entries(draft),'saved_ids':[str(x) for x in selected]})


@access('view_planningdraft')
@require_http_methods(['GET'])
@transaction.atomic
def excel(request,pk):
    draft=get_object_or_404(PlanningDraft.objects.select_related('course'),pk=pk)
    service.require(request.user,'view_employee')
    try:
        if request.GET.get('all')=='1':
            from .attendance_excel import daily_archive
            data,_=service.validate(draft,preparation=True)
            content=daily_archive(data['dates'],lambda day:service.preparation_excel(draft,day),'PREPARACAO-RASCUNHO')
            filename=f'PREPARACAO-RASCUNHO-{draft.pk}.zip';mime='application/zip'
        else:
            try:day=date.fromisoformat(request.GET.get('date',''))
            except ValueError:raise ValidationError('Seleccione um dia válido do calendário do rascunho.')
            content=service.preparation_excel(draft,day)
            filename=f'PREPARACAO-RASCUNHO-{draft.pk}-{day:%Y-%m-%d}.xlsx'
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    except ValidationError as exc:
        blockers=[]
        for item in service.entries(draft):
            try:require_admission(item)
            except ValidationError as reason:
                item['reason']=' '.join(reason.messages);blockers.append(item)
        return render(request,'planning/attendance_blocked.html',{'draft':draft,'errors':exc.messages,'blockers':blockers},status=400)
    return FileResponse(io.BytesIO(content),as_attachment=True,filename=filename,content_type=mime)


class DecisionForm(forms.Form):
    reason=forms.CharField(label='Justificação',min_length=8,max_length=2000,widget=forms.Textarea)
    reference=forms.CharField(label='Referência documental / autorização',min_length=3,max_length=1000)
    confirm=forms.BooleanField(label='Confirmo esta autorização específica; não confirma presença nem certificado.')


@access('authorize_training_repeat')
@require_http_methods(['GET','POST'])
def decision(request,employee_pk,group_pk=None,draft_pk=None):
    group=get_object_or_404(TrainingGroup,pk=group_pk) if group_pk else None
    draft=get_object_or_404(PlanningDraft,pk=draft_pk,state='draft') if draft_pk else None
    course=group.course if group else service.draft_course(draft)
    person=get_object_or_404(Employee,pk=employee_pk)
    item=assess(course,[person],group=group,draft=draft)[0]
    payload={'user':request.user.pk,'employee':person.pk,'group':group.pk if group else None,
        'draft':str(draft.pk) if draft else None,'target_revision':group.revision if group else draft.revision,
        'basis':item['basis'],'kind':item['decision_kind']}
    form=DecisionForm(request.POST or None)
    token=signing.dumps(payload,salt=DECISION_SALT)
    if request.method=='POST' and form.is_valid():
        try:
            supplied=signing.loads(request.POST.get('token',''),salt=DECISION_SALT,max_age=900)
            if supplied!=payload or not item['decision_kind']:raise ValidationError('Proposta alterada ou análise já desnecessária. Reabra a página.')
            with transaction.atomic():
                if course.pk:
                    Course.objects.filter(pk=course.pk).update(next_group=F('next_group'))
                    course.refresh_from_db()
                if draft:service.lock(draft,draft.revision)
                if group and TrainingGroup.objects.filter(pk=group.pk,state='Planeada',revision=group.revision).update(revision=F('revision')+1)!=1:
                    raise ValidationError('A turma mudou ou já iniciou.')
                fresh=assess(course,[person],group=group,draft=draft)[0]
                if fresh['basis']!=supplied['basis'] or fresh['authorization']:raise ValidationError('Histórico alterado ou decisão já registada.')
                result=EligibilityDecision.objects.create(course=course if course.pk else None,employee=person,group=group,draft=draft,
                    kind=fresh['decision_kind'],basis=fresh['basis'],actor=request.user,reason=form.cleaned_data['reason'],reference=form.cleaned_data['reference'])
                service.audit(request.user,'ELIGIBILITY_AUTHORIZATION',decision=result.pk,proposal=payload,reason=result.reason,reference=result.reference)
            return redirect('group_enrol',pk=group.pk) if group else redirect('planning_draft_people',pk=draft.pk)
        except (ValidationError,signing.BadSignature,IntegrityError,OperationalError) as exc:form.add_error(None,str(exc))
    return render(request,'planning/eligibility_decision.html',{'form':form,'token':token,'item':item,'course':course})

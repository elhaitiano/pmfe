"""Explicit new delivery supplements; immutable historical source and group binding."""
import json
from django import forms
from django.contrib.auth.decorators import login_required, permission_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction, OperationalError, IntegrityError
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from . import documents
from .models import Training, TrainingGroup, HistoricalInterfaceLink, TrainingDocumentState

SALT='pmfe.planning-delivery.v1'
class DeliveryForm(forms.Form):
    date=forms.DateField(label='Data da entrega',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    responsible=forms.CharField(label='Responsável pela entrega',max_length=150)
    reference=forms.CharField(label='Referência da evidência administrativa',max_length=500)
    justification=forms.CharField(label='Fundamentação',min_length=10,max_length=2000,widget=forms.Textarea)
    confirm=forms.BooleanField(label='Confirmo a emissão e a entrega deste certificado ao formando e participação indicados')
    token=forms.CharField(widget=forms.HiddenInput)

def bound_training(pk,scope,group_id):
    t=get_object_or_404(Training.objects.select_related('employee'),pk=pk)
    if scope=='group':
        from .group_roster import group_trainings
        g=get_object_or_404(TrainingGroup,pk=group_id)
        if not group_trainings(g).filter(pk=t.pk).exists():raise ValidationError('A participação não pertence a este grupo.')
    elif scope=='edition':
        if not HistoricalInterfaceLink.objects.filter(training=t,participation__active=True,participation__edition_id=group_id,participation__edition__active=True).exists():raise ValidationError('A participação não pertence a esta edição.')
    else:raise ValidationError('Grupo inválido.')
    return t

def snapshot(t,scope,group_id,user):
    documents.require(user,'change_trainingdocumentstate')
    if not user.has_perms(['training.view_training','training.view_employee']):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    t=bound_training(t.pk,scope,group_id)
    state=TrainingDocumentState.objects.filter(training=t).first()
    if t.delivery_reported or state and state.learner_delivered is True:raise ValidationError('Entrega já confirmada. O registo existente será preservado.')
    if t.certificate_counted_elsewhere:raise ValidationError('O mesmo certificado já está associado a outra participação. Registo preservado.')
    if t.status!='Concluído' or not t.end or t.end>timezone.localdate():raise ValidationError('A entrega exige uma formação concluída e terminada.')
    return {'user':user.pk,'training':t.pk,'employee':t.employee_id,'version':t.version,'scope':scope,'group':int(group_id),'state':model_to_dict(state) if state else None}

@transaction.atomic
def confirm_delivery(user,pk,scope,group_id,data):
    documents.require(user,'change_trainingdocumentstate')
    pending=documents.audit(user,'PLANNING_DELIVERY_PENDING',{'training_id':pk})
    t=bound_training(pk,scope,group_id)
    form=DeliveryForm(data)
    if not form.is_valid():raise ValidationError(form.errors.as_json())
    try:payload=signing.loads(form.cleaned_data['token'],salt=SALT,max_age=900)
    except signing.BadSignature:raise ValidationError('Confirmação expirada ou inválida. Reabra a página.')
    current=snapshot(t,scope,group_id,user)
    # JSON round-trip normalises dates in the signed state snapshot.
    if payload!=json.loads(json.dumps(current,default=str)):raise ValidationError('A participação ou estado mudou. Reabra a página.')
    state=TrainingDocumentState.objects.filter(training=t).first()
    values={k:getattr(state,k) if state else None for k in ['issued','learner_delivered','learner_date','learner_responsible','hr_delivered','hr_date','hr_responsible','observations','reference']}
    for key in ['learner_responsible','hr_responsible','observations','reference']:values[key]=values[key] or ''
    for key in ['issued','learner_delivered','hr_delivered']:values[key]='true' if values[key] is True else 'false' if values[key] is False else 'unknown'
    values.update(issued='true',learner_delivered='true',learner_date=form.cleaned_data['date'].isoformat(),learner_responsible=form.cleaned_data['responsible'],reference=form.cleaned_data['reference'],revision=state.revision if state else 0,justification=form.cleaned_data['justification'],confirm='on')
    result=documents.save_delivery(user,t,values)
    pending.action='PLANNING_CERTIFICATE_DELIVERY_CONFIRMED'
    pending.detail=json.dumps({'training_id':pk,'employee_id':t.employee_id,'scope':scope,'group_id':int(group_id),'reference':form.cleaned_data['reference'],'justification':form.cleaned_data['justification'],'historical_training_unchanged':True},ensure_ascii=False)
    pending.save(update_fields=['action','detail'])
    return result

@login_required
@permission_required(['training.change_trainingdocumentstate','training.view_training','training.view_employee'],raise_exception=True)
@require_http_methods(['GET','POST'])
def review(request,pk):
    # This endpoint writes only the document supplement; do not trigger legacy
    # interface reconciliation after its POST.
    request.group_validation_managed=True
    documents.require(request.user,'change_trainingdocumentstate')
    scope=request.GET.get('scope','');group_id=request.GET.get('group','')
    from django.http import HttpResponseBadRequest
    if not group_id.isdigit():return HttpResponseBadRequest('Grupo inválido.')
    try:t=bound_training(pk,scope,group_id)
    except ValidationError:return HttpResponseBadRequest('Participação ou grupo inválido.')
    error='';success=False;available=False;status=200
    form=DeliveryForm(request.POST if request.method=='POST' else None)
    try:
        payload=snapshot(t,scope,group_id,request.user);available=True
        if request.method=='POST' and form.is_valid():
            confirm_delivery(request.user,pk,scope,group_id,request.POST);success=True;available=False
        elif request.method=='GET':form.initial['token']=signing.dumps(json.loads(json.dumps(payload,default=str)),salt=SALT)
    except (ValidationError,OperationalError,IntegrityError) as exc:
        error=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Operação concorrente. Reabra a página.'
        status=409 if request.method=='POST' else 200
    return render(request,'planning/certificate_delivery.html',{'training':t,'form':form,'available':available,'success':success,'error':error,'delivery_scope':scope,'delivery_group':group_id},status=status)

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.http import Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from .edit_navigation import saved_response
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.db import OperationalError
from django.core import signing
from . import data_validation as service

LABELS={'identity':'Identidade','roster':'Cadastro actual','category':'Categoria profissional','function':'Função exercida','participation':'Participação documental','attendance':'Assiduidade','completion':'Conclusão','evaluation':'Avaliação','issued':'Emissão','learner_delivered':'Entrega ao formando','hr_delivered':'Entrega aos RH'}
class DecisionForm(forms.Form):
    dimension=forms.ChoiceField(label='Informação a verificar')
    state=forms.ChoiceField(label='Estado',choices=list(service.STATES.items()))
    reason=forms.CharField(label='Justificação',max_length=2000,widget=forms.Textarea(attrs={'rows':3}))
    evidence=forms.CharField(label='Referência documental (fonte, hash, aba/linha ou documento)',max_length=2000)
    def __init__(self,kind,*a,**kw):
        super().__init__(*a,**kw);self.fields['dimension'].choices=[(k,LABELS[k]) for k in service.DIMENSIONS[kind]]

def require(user,kind):
    if not service.enabled():raise Http404
    service.model(kind)
    if not service.can_review(user,kind):raise PermissionDenied

def label(obj,kind):
    return f'{obj.employee.number} — {obj.employee.name} / {obj.title}' if kind=='training' else str(obj)

@login_required
@never_cache
@require_http_methods(['GET'])
def index(request):
    kind=request.GET.get('kind','training');require(request.user,kind);p=service.policy()
    dimension=request.GET.get('dimension','participation' if kind=='training' else 'identity')
    if dimension not in service.DIMENSIONS[kind]:raise Http404
    entries=[];show_duplicates=request.GET.get('scope')=='duplicates'
    objects=service.model(kind).objects.all()
    if kind=='training':objects=objects.select_related('employee')
    for obj in objects:
        duplicate=kind=='training' and p.duplicate(obj.pk)
        if duplicate:
            if show_duplicates:entries.append({'record':label(obj,kind),'pk':obj.pk,'state':'Duplicado documentado — preservado','evidence':'Referência de consolidação preservada no manifesto de evidência.'})
            continue
        if show_duplicates:continue
        decision=p.decision(kind,obj.pk,dimension)
        if kind=='training' and dimension=='participation' and not p.participation(obj.pk) and decision['state']=='confirmed':decision={**decision,'state':'pending','label':'Identidade do trabalhador por validar'}
        if decision['state']!='confirmed':entries.append({'record':label(obj,kind),'pk':obj.pk,'state':decision['label'],'evidence':decision['evidence']})
    from django.core.paginator import Paginator
    return render(request,'validation/index.html',{'kind':kind,'dimension':dimension,'page':Paginator(entries,30).get_page(request.GET.get('page')),'pending_count':len(entries),'dimensions':[(k,LABELS[k]) for k in service.DIMENSIONS[kind]]})

@login_required
@never_cache
@require_http_methods(['GET','POST'])
def decision(request,kind,pk):
    require(request.user,kind);obj=get_object_or_404(service.model(kind),pk=pk)
    form=DecisionForm(kind,request.POST if request.method=='POST' and not request.POST.get('token') else None)
    context={'record':label(obj,kind),'kind':kind,'form':form}
    if request.method=='POST':
        try:
            if request.POST.get('token'):
                if request.POST.get('confirm')!='yes':raise ValidationError('Confirme expressamente a decisão.')
                payload=signing.loads(request.POST['token'],salt=service.ACTION,max_age=600)
                if payload['kind']!=kind or payload['pk']!=pk:raise ValidationError('Registo divergente da proposta.')
                service.confirm(request.user,request.POST['token']);return saved_response(request,reverse('validation_index'))
            if form.is_valid():
                token,payload=service.preview(request.user,kind,pk,**form.cleaned_data)
                context.update(token=token,proposal=payload,proposal_state=service.STATES[payload['state']],dimension_label=LABELS[payload['dimension']])
        except signing.BadSignature:context['error']='Proposta inválida ou expirada.'
        except (ValidationError,OperationalError) as e:context['error']=str(e) if isinstance(e,ValidationError) else 'Operação concorrente. Reabra a proposta.'
    return render(request,'validation/decision.html',context)

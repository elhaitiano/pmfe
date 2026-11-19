from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import OperationalError
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.http import HttpResponseBadRequest
from django.views.decorators.http import require_http_methods,require_GET
from django.views.decorators.cache import never_cache
from .models import FormationSurvey,FormationSurveyResponse,FormationSurveyAggregate,FormationQuestionnaire,Course,TrainingGroup
from . import formation_survey_service as service
from .formation_survey_forms import SurveyForm,SurveyConfigForm,EntryForm,PrintForm,style
from .formation_survey_exports import export,response
from .formation_survey_pdf import form_pdf

class Filters(forms.Form):
    course=forms.ModelChoiceField(label='Formação',queryset=Course.objects.order_by('title'),required=False)
    group=forms.ModelChoiceField(label='Grupo',queryset=TrainingGroup.objects.order_by('-start'),required=False)
    start=forms.DateField(label='Avaliações desde',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='Até',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    trainer=forms.CharField(label='Formador',required=False,max_length=150)
    questionnaire=forms.ModelChoiceField(label='Questionário',queryset=FormationQuestionnaire.objects.all(),required=False)
    def __init__(self,*a,**kw):super().__init__(*a,**kw);style(self)
    def clean(self):
        d=super().clean()
        if d.get('start') and d.get('end') and d['start']>d['end']:raise forms.ValidationError('O fim não pode anteceder o início.')
        if d.get('group') and d.get('course') and d['group'].course_id!=d['course'].pk:raise forms.ValidationError('O grupo não pertence à formação seleccionada.')
        return d

def access(request,permission='view_formationsurvey'):
    service.require(request.user,permission)
    if request.method=='POST':request.formation_survey_managed=True

def base_query():return FormationSurvey.objects.select_related('group__course','questionnaire').prefetch_related('responses','aggregate')

@login_required
@require_GET
@never_cache
def index(request):
    access(request);form=Filters(request.GET);surveys=base_query()
    if form.is_valid():
        d=form.cleaned_data
        if d.get('course'):surveys=surveys.filter(group__course=d['course'])
        if d.get('group'):surveys=surveys.filter(group=d['group'])
        if d.get('start'):surveys=surveys.filter(evaluation_date__gte=d['start'])
        if d.get('end'):surveys=surveys.filter(evaluation_date__lte=d['end'])
        if d.get('trainer'):surveys=surveys.filter(group__trainer__icontains=d['trainer'])
        version=d.get('questionnaire') or FormationQuestionnaire.objects.filter(code='proagua-formation-v1').first()
        if version:surveys=surveys.filter(questionnaire=version)
    else:surveys=surveys.none()
    summary=service.summary(surveys)
    if request.GET.get('export'):
        access(request,'export_formation_evaluation')
        if not form.is_valid():return render(request,'formation_surveys/index.html',{'form':form,'summary':summary},status=400)
        if request.GET['export'] not in ['pdf','xlsx']:return HttpResponseBadRequest('Formato inválido.')
        return export(summary,request.GET['export'])
    query=request.GET.copy();query.pop('export',None)
    from django.core.paginator import Paginator
    return render(request,'formation_surveys/index.html',{'form':form,'summary':summary,'page':Paginator(surveys.order_by('-evaluation_date','pk'),20).get_page(request.GET.get('page')),'query':query.urlencode()},status=200 if form.is_valid() else 400)

@login_required
@require_http_methods(['GET','POST'])
@never_cache
def create(request):
    access(request,'add_formationsurvey')
    form=SurveyForm(request.POST if request.method=='POST' else None,initial={'group':request.GET.get('group'),'anonymous':True,'mode':'individual','questionnaire':FormationQuestionnaire.objects.filter(code='proagua-formation-v1').first()})
    if request.method=='POST' and form.is_valid():
        try:
            survey=service.create_survey(request.user,**form.cleaned_data);messages.success(request,'Avaliação criada. Pode registar as fichas.');return redirect('formation_survey_detail',pk=survey.pk)
        except (ValidationError,OperationalError) as exc:form.add_error(None,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Operação concorrente. Tente novamente.')
    return render(request,'formation_surveys/create.html',{'form':form})

@login_required
@require_GET
@never_cache
def detail(request,pk):
    access(request);survey=get_object_or_404(base_query(),pk=pk);summary=service.summary([survey])
    rows=survey.responses.all().order_by('reference') if survey.mode=='individual' and request.user.has_perm('training.view_formationsurveyresponse') else []
    aggregate=getattr(survey,'aggregate',None)
    query='group='+str(survey.group_id)
    return render(request,'formation_surveys/detail.html',{'survey':survey,'summary':summary,'entries':rows,'aggregate':aggregate,'export_query':query})

@login_required
@require_http_methods(['GET','POST'])
@never_cache
def entry(request,pk,entry_pk=None):
    access(request);survey=get_object_or_404(base_query(),pk=pk);survey.clean()
    model=FormationSurveyResponse if survey.mode=='individual' else FormationSurveyAggregate
    access(request,('change_' if entry_pk else 'add_')+model._meta.model_name)
    if not survey.anonymous:access(request,'view_employee')
    obj=get_object_or_404(model,survey=survey,pk=entry_pk) if entry_pk else None
    if not entry_pk and survey.mode=='aggregate' and hasattr(survey,'aggregate'):return redirect('formation_survey_entry_edit',pk=pk,entry_pk=survey.aggregate.pk)
    initial={'state':'completed','revision':obj.revision if obj else 0}
    if obj:
        initial.update({k:getattr(obj,k) for k in ['state','improvements','future_training']})
        if survey.mode=='individual':initial.update(reference=obj.reference,employee=obj.employee_id,**obj.answers)
        else:
            initial['received_forms']=obj.received_forms
            for key,values in obj.counts.items():
                for i,v in enumerate(values,1):initial[key+'_'+str(i)]=v
    form=EntryForm(survey,request.user,request.POST if request.method=='POST' else None,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            if survey.anonymous and request.POST.get('employee'):raise ValidationError('Uma ficha anónima não aceita identificação nominal.')
            saved=service.save_entry(request.user,pk,form.payload(),pk=entry_pk,revision=form.cleaned_data['revision'],reason=form.cleaned_data['reason'])
            messages.success(request,'Avaliação guardada com sucesso.' if saved.state=='completed' else 'Rascunho guardado; excluído dos resultados.')
            return redirect('formation_survey_detail',pk=pk)
        except (ValidationError,OperationalError) as exc:form.add_error(None,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Operação concorrente. Reabra a ficha.')
    return render(request,'formation_surveys/entry.html',{'survey':survey,'form':form,'editing':obj is not None})

@login_required
@require_http_methods(['GET','POST'])
@never_cache
def configure(request,pk):
    access(request,'change_formationsurvey');survey=get_object_or_404(base_query(),pk=pk)
    form=SurveyConfigForm(request.POST if request.method=='POST' else None,instance=survey,initial={'revision':survey.revision})
    if request.method=='POST' and form.is_valid():
        try:
            service.configure_survey(request.user,pk,form.cleaned_data,form.cleaned_data['revision'],form.cleaned_data['reason']);messages.success(request,'Configuração guardada.');return redirect('formation_survey_detail',pk=pk)
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages))
    return render(request,'formation_surveys/create.html',{'form':form,'editing':True})

@login_required
@require_GET
@never_cache
def printing(request):
    access(request,'print_formation_evaluation');form=PrintForm(request.GET if request.GET.get('download') else None,initial={'group':request.GET.get('group'),'kind':'group' if request.GET.get('group') else 'blank'})
    if not request.user.has_perms(['training.print_personal_evaluation','training.view_employee']):
        form.fields['personal'].disabled=True;form.fields['contacts'].disabled=True;form.fields['employee'].queryset=form.fields['employee'].queryset.none();form.fields['kind'].choices=[c for c in form.fields['kind'].choices if c[0]!='person']
    if request.GET.get('download') and form.is_valid():
        try:
            d=form.cleaned_data;raw=form_pdf(request.user,d['kind'],d.get('group'),d['employee'].pk if d.get('employee') else None,d['personal'],d['contacts'])
            return response(raw,'pdf','PROAGUA_Ficha_Avaliacao_Formacao')
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages))
    return render(request,'formation_surveys/print.html',{'form':form})

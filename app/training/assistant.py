"""Admin-managed local guidance. No generative AI or external requests."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from .models import AssistantGuide
from .views import staff_required, record

ROUTES = [('home','Gestão de Formação'),('planning','Planeamento e Grupos'),
          ('batch_index','Presenças, notas e certificados'),('dashboard','Dashboard Executivo'),
          ('executive_reports','Relatórios e arquivo'),('employee_data_select','Ficha Individual')]

class GuideForm(forms.ModelForm):
    keyword_text = forms.CharField(label='Perguntas e palavras de pesquisa', widget=forms.Textarea(attrs={'rows':4}), help_text='Uma expressão por linha. Ex.: imprimir lista; criar grupo. Use linhas separadas.')
    route = forms.ChoiceField(label='Página de destino',choices=ROUTES)
    revision = forms.IntegerField(widget=forms.HiddenInput,required=False)
    class Meta:
        model=AssistantGuide
        fields=['slug','title','route','staff_only','enabled']
        labels={'slug':'Código único do assunto','title':'Título da orientação','staff_only':'Operação reservada ao administrador','enabled':'Publicar este assunto no assistente'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk:
            self.fields['slug'].disabled=True
            self.initial['keyword_text']='\n'.join(self.instance.keywords)
            self.initial['revision']=self.instance.revision
    def clean_keyword_text(self):
        words=[line.strip() for line in self.cleaned_data['keyword_text'].splitlines() if line.strip()]
        if not 1<=len(words)<=40 or any(len(x)>120 for x in words):
            raise forms.ValidationError('Indique entre 1 e 40 expressões, com até 120 caracteres por linha.')
        return list(dict.fromkeys(words))
    def clean(self):
        data=super().clean()
        if data.get('route')=='batch_index' and not data.get('staff_only'):
            self.add_error('staff_only','A grelha de resultados é reservada a administradores.')
        return data

class StepForm(forms.Form):
    text=forms.CharField(label='Instrução do passo',max_length=1500,widget=forms.Textarea(attrs={'rows':3}))
    target=forms.CharField(label='Controlo a destacar (opcional)',required=False,max_length=200,
        help_text='Identificador CSS do controlo. Deixe em branco para uma orientação apenas em texto.')
    def clean_target(self):
        value=self.cleaned_data['target'].strip()
        # Only simple ID or tag/attribute selectors used by this platform.
        import re
        if value and not re.fullmatch(r'(#[A-Za-z][\w-]*|[a-z][a-z0-9-]*(?:\[[a-z][\w-]*(?:[\^$*]?="[^"<>\\]{1,140}")?\]){0,3})(?: (?:button|input|select|a))?',value):
            raise forms.ValidationError('Use um identificador como #personSearch ou um selector simples como select[name="date"].')
        return value or None

StepSet=forms.formset_factory(StepForm,extra=2,max_num=20,validate_max=True,absolute_max=25,can_delete=True)

@login_required
@require_GET
def guides(request):
    payload=[{'id':g.slug,'title':g.title,'keys':g.keywords,'route':g.route,
              'steps':g.steps,'staff':g.staff_only,'revision':g.revision}
             for g in AssistantGuide.objects.filter(enabled=True)]
    response=JsonResponse(payload,safe=False)
    response['Cache-Control']='no-store, private'
    return response

@login_required
@staff_required
@require_GET
def index(request):
    return render(request,'assistant/index.html',{'guides':AssistantGuide.objects.all()})

@login_required
@staff_required
@require_http_methods(['GET','POST'])
def edit(request,pk=None):
    guide=get_object_or_404(AssistantGuide,pk=pk) if pk else AssistantGuide()
    form=GuideForm(request.POST or None,instance=guide)
    steps=StepSet(request.POST or None,initial=guide.steps,prefix='steps')
    error=''
    if request.method=='POST':
        valid_form=form.is_valid();valid_steps=steps.is_valid()
        if valid_form and valid_steps:
            items=[{'text':s.cleaned_data['text'],'target':s.cleaned_data.get('target')}
                   for s in steps if s.cleaned_data and not s.cleaned_data.get('DELETE')]
            if not items:error='Acrescente pelo menos um passo de orientação.'
            else:
                with transaction.atomic():
                    obj=form.save(commit=False)
                    if pk:
                        current=AssistantGuide.objects.select_for_update().get(pk=pk)
                        if current.revision!=form.cleaned_data.get('revision'):
                            error='Outro administrador actualizou este assunto. Abra novamente antes de guardar.'
                        else:obj.revision=current.revision+1
                    if not error:
                        obj.steps=items;obj.keywords=form.cleaned_data['keyword_text'];obj.actor=request.user.username
                        obj.save()
                        record(request,'ASSISTENTE',f'{obj.slug} · versão {obj.revision} · '+('publicado' if obj.enabled else 'desactivado'))
                        messages.success(request,'Orientação guardada. Os utilizadores recebem a versão actualizada ao abrir a ajuda ou actualizar a página.')
                        return redirect('assistant_admin')
    return render(request,'assistant/edit.html',{'form':form,'steps':steps,'guide':guide,'error':error})

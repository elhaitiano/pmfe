from django import forms
from .models import FormationQuestionnaire,FormationSurvey,TrainingGroup,Employee
from .formation_survey_service import group_people

def style(form):
    for field in form.fields.values():
        if not isinstance(field.widget,(forms.CheckboxInput,forms.RadioSelect)):field.widget.attrs['class']='gf-field'

class SurveyForm(forms.ModelForm):
    class Meta:
        model=FormationSurvey
        fields=['group','questionnaire','evaluation_date','mode','anonymous']
        labels={'group':'Grupo','questionnaire':'Questionário','evaluation_date':'Data da avaliação','mode':'Método','anonymous':'Avaliação anónima'}
        widgets={'evaluation_date':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.fields['group'].queryset=TrainingGroup.objects.select_related('course').order_by('-start','pk');self.fields['questionnaire'].queryset=FormationQuestionnaire.objects.filter(active=True);style(self)
    def _post_clean(self):
        # The write service fills and validates the frozen digest atomically.
        questionnaire=self.cleaned_data.get('questionnaire')
        if questionnaire:self.instance.questionnaire_digest=questionnaire.schema_digest
        super()._post_clean()

class EntryForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput,initial=0,min_value=0)
    state=forms.ChoiceField(label='Estado',choices=[('completed','Concluir registo'),('draft','Guardar rascunho')])
    reason=forms.CharField(label='Motivo da correcção',required=False,max_length=2000)
    improvements=forms.CharField(label='Que sugestões de melhoria gostaria de fazer?',required=False,max_length=4000,widget=forms.Textarea(attrs={'rows':3}))
    future_training=forms.CharField(label='Que outras acções de formação gostaria de frequentar?',required=False,max_length=4000,widget=forms.Textarea(attrs={'rows':3}))
    def __init__(self,survey,user,*a,**kw):
        self.survey=survey;super().__init__(*a,**kw)
        if not user.has_perm('training.save_partial_survey'):self.fields['state'].choices=[('completed','Concluir registo')]
        if survey.mode=='individual':
            self.fields['reference']=forms.CharField(label='Código da ficha',max_length=80)
            if not survey.anonymous:self.fields['employee']=forms.ModelChoiceField(label='Formando',queryset=group_people(survey.group))
            for q in survey.questionnaire.schema['questions']:
                options=[('', 'Sem resposta')]+([(1,'Não'),(5,'Sim')] if q['kind']=='yesno' else [(i,str(i)) for i in range(1,6)])
                self.fields[q['key']]=forms.TypedChoiceField(label=q['text'],required=False,coerce=int,empty_value=None,choices=options,widget=forms.RadioSelect)
        else:
            self.fields['received_forms']=forms.IntegerField(label='Fichas recebidas',min_value=0,max_value=100000)
            for q in survey.questionnaire.schema['questions']:
                for i in ([1,5] if q['kind']=='yesno' else range(1,6)):
                    self.fields[q['key']+'_'+str(i)]=forms.IntegerField(label=str(i),required=False,min_value=0,max_value=100000,widget=forms.NumberInput(attrs={'inputmode':'numeric','aria-label':q['text']+' — '+('Não' if i==1 else 'Sim') if q['kind']=='yesno' else q['text']+' — '+str(i)}))
        style(self)
    def payload(self):
        d=self.cleaned_data;out={k:d[k] for k in ['state','improvements','future_training']}
        if self.survey.mode=='individual':out.update(reference=d['reference'],employee_id=d['employee'].pk if d.get('employee') else None,answers={q['key']:d[q['key']] for q in self.survey.questionnaire.schema['questions']})
        else:
            counts={}
            for q in self.survey.questionnaire.schema['questions']:
                counts[q['key']]=[d.get(q['key']+'_'+str(i)) if q['kind']!='yesno' or i in [1,5] else 0 for i in range(1,6)]
            out.update(received_forms=d['received_forms'],counts=counts,reason=d['reason'])
        return out
    @property
    def sections(self):
        result=[]
        for section in self.survey.questionnaire.schema['sections']:
            rows=[]
            for q in self.survey.questionnaire.schema['questions']:
                if q['section']!=section['key']:continue
                rows.append(dict(question=q,fields=[self[q['key']]] if self.survey.mode=='individual' else [self[q['key']+'_'+str(i)] for i in ([1,5] if q['kind']=='yesno' else range(1,6))]))
            result.append(dict(**section,rows=rows))
        return result

class SurveyConfigForm(SurveyForm):
    revision=forms.IntegerField(widget=forms.HiddenInput,min_value=1)
    reason=forms.CharField(label='Motivo da correcção',max_length=2000)
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.fields['group'].disabled=True
        if self.instance.responses.exists() or hasattr(self.instance,'aggregate'):
            for name in ['questionnaire','mode','anonymous']:self.fields[name].disabled=True

class PrintForm(forms.Form):
    group=forms.ModelChoiceField(label='Grupo',queryset=TrainingGroup.objects.all(),required=False)
    kind=forms.ChoiceField(label='Ficha',choices=[('blank','Em branco'),('group','Pré-preenchida — grupo, sem identificação'),('person','Pré-preenchida — um formando'),('batch','Lote de fichas dos participantes')])
    employee=forms.ModelChoiceField(label='Formando (apenas ficha individual)',queryset=Employee.objects.none(),required=False)
    personal=forms.BooleanField(label='Incluir identificação nominal (permissão adicional)',required=False)
    contacts=forms.BooleanField(label='Incluir contactos disponíveis',required=False)
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        group_id=self.data.get('group') or self.initial.get('group')
        if str(group_id).isdigit():
            group=TrainingGroup.objects.filter(pk=group_id).first()
            if group:self.fields['employee'].queryset=group_people(group)
        style(self)
    def clean(self):
        d=super().clean()
        if d.get('kind')!='blank' and not d.get('group'):self.add_error('group','Seleccione o grupo.')
        if d.get('kind')=='person' and not d.get('personal'):self.add_error('personal','Confirme expressamente a inclusão de dados nominais.')
        if d.get('contacts') and not d.get('personal'):self.add_error('contacts','Contactos exigem identificação nominal autorizada.')
        return d

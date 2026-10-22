from django import forms
from .models import Employee, Training


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs['class'] = 'gf-field' if not isinstance(field.widget, forms.CheckboxInput) else 'rounded bg-slate-900 border-slate-700 text-secondary'

    @property
    def design_groups(self):
        return [('', self.visible_fields())]


class EmployeeForm(StyledModelForm):
    phone_secondary = forms.CharField(label='Telefone 2', required=False, max_length=80)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        source = self.instance.source_data or {}
        self.initial['phone_secondary'] = source.get('Telefone 2', '')

    def save(self, commit=True):
        employee = super().save(commit=False)
        employee.source_data = dict(employee.source_data or {})
        employee.source_data.update({'Função': self.cleaned_data['function'],
                                    'Telefone 2': self.cleaned_data['phone_secondary']})
        employee.source_data['Validação Cadastro EPAL'] = self.cleaned_data['registration_validation']
        if commit:
            employee.save()
        return employee

    @property
    def design_groups(self):
        return [(title, [self[name] for name in names]) for title, names in [
            ('Identificação', ['number', 'name', 'gender', 'birth_date',
                              'identity_number', 'social_security_number', 'marital_status']),
            ('Contactos', ['phone', 'phone_secondary', 'email']),
            ('Dados profissionais', ['admission_date', 'company', 'direction',
                                    'function', 'category', 'area', 'cost_center']),
            ('Cadastro e revisão', ['registration_origin', 'review_status',
                                   'registration_validation', 'source_row']),
        ]]

    class Meta:
        model = Employee
        fields = ['number', 'name', 'gender', 'birth_date', 'identity_number',
                  'social_security_number', 'marital_status', 'phone', 'email',
                  'admission_date', 'company', 'direction', 'function', 'category',
                  'area', 'cost_center', 'registration_origin', 'review_status',
                  'registration_validation', 'source_row']
        widgets = {key: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})
                   for key in ['birth_date', 'admission_date']}
        widgets['registration_origin'] = forms.Textarea(attrs={'rows': 2})


class TrainingForm(StyledModelForm):
    certified = forms.TypedChoiceField(label='Certificado entregue', required=False,
        choices=[('unknown', 'Por confirmar'), ('true', 'Sim'), ('false', 'Não')],
        coerce=lambda value: {'unknown': None, 'true': True, 'false': False}[value], empty_value=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['certified'] = 'true' if self.instance.certified is True else 'false' if self.instance.certified is False else 'unknown'
        if not self.instance.pk:
            self.initial['status'] = 'Planeado'
        if self.instance.group_id:
            for name in ['title','area','trainer','provider','location','start','end','days','hours_per_day']:
                if name in self.fields:self.fields[name].disabled=True
        if 'attendance_confirmed' in self.fields:
            self.fields['attendance_confirmed'].help_text='Marque após verificar a lista assinada. Zero só representa ausência confirmada quando esta opção está marcada.'
        self.fields['grade'].help_text = 'Nota final de 0 a 20. Não é calculada pela assiduidade.'
        self.fields['result'].help_text = 'Decisão da avaliação final; não é inferida a partir da nota.'
        if 'days' in self.fields:
            self.fields['days'].help_text = 'Dias de formação previstos, que podem não ser consecutivos.'

    revision = forms.IntegerField(widget=forms.HiddenInput, required=False)

    @property
    def design_groups(self):
        return [(title, [self[name] for name in names]) for title, names in [
            ('', ['title', 'area', 'trainer', 'provider', 'start', 'end']),
            ('Duração da formação', ['days', 'hours_per_day']),
            ('Participação e resultados', ['location', 'attendance', 'attendance_confirmed', 'grade', 'result', 'status']),
            ('Certificado entregue', ['certificate_reference', 'certified', 'certificate_delivery_date', 'observations']),
        ]]

    class Meta:
        model = Training
        fields = ['title', 'area', 'trainer', 'provider', 'location', 'start', 'end',
                  'days', 'hours_per_day', 'attendance', 'attendance_confirmed', 'grade', 'result', 'certified', 'status', 'certificate_reference', 'certificate_delivery_date', 'observations']
        widgets = {key: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}) for key in ['start', 'end', 'certificate_delivery_date']}

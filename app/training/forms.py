from django import forms
from django.conf import settings
import re
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
        self.initial['function'] = self.instance.function or source.get('Função', '')
        self.initial['phone_secondary'] = source.get('Telefone 2', '')
        if self.instance.pk and getattr(settings, 'PMF_DOCUMENTS_ENABLED', False):
            for name in ('category', 'function'):
                self.fields[name].disabled = True
                self.fields[name].help_text = 'Utilize «Corrigir categoria e função» na ficha para uma alteração fundamentada e auditada.'

    def _phone(self, value, previous):
        if value == previous or not value:
            return value
        if not re.fullmatch(r'\+?[0-9 ()\-.]+', value) or not 7 <= len(re.sub(r'\D', '', value)) <= 15:
            raise forms.ValidationError('Indique um contacto válido, com 7 a 15 algarismos e prefixo internacional opcional.')
        return value

    def clean_phone(self):
        return self._phone(self.cleaned_data['phone'], self.instance.phone)

    def clean_phone_secondary(self):
        return self._phone(self.cleaned_data['phone_secondary'], (self.instance.source_data or {}).get('Telefone 2', ''))

    def save(self, commit=True):
        employee = super().save(commit=False)
        employee.source_data = dict(employee.source_data or {})
        employee.source_data['Telefone 2'] = self.cleaned_data['phone_secondary']
        if not getattr(settings, 'PMF_DOCUMENTS_ENABLED', False):
            employee.source_data['Função'] = employee.function
        if commit:
            if employee.pk and getattr(settings, 'PMF_DOCUMENTS_ENABLED', False):
                employee.save(update_fields=[name for name in self.Meta.fields if name not in ('category', 'function')] + ['source_data'])
                employee.refresh_from_db()
            else:
                employee.save()
        return employee

    class Meta:
        model = Employee
        fields = ['number', 'name', 'direction', 'category', 'function', 'area', 'email', 'phone', 'birth_date', 'admission_date']
        widgets = {key: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})
                   for key in ['birth_date', 'admission_date']}


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
        if self.instance.pk:
            historical=str(self.instance.source_key or '').startswith('pilar3b:')
            from .models import AuditEvent
            supplement=historical or self.instance.grade is not None or AuditEvent.objects.filter(
                action__in=['EPAL_EVALUATION_APPROVED','EVALUATION_CORRECTION'],
                detail__contains=f'"training_id": {self.instance.pk},').exists()
            if supplement:
                self.fields['grade'].disabled=True
                self.fields['grade'].help_text='Utilize «Corrigir avaliação» em Consultas e Relatórios para uma alteração fundamentada e auditada.'
            if historical:
                for name in ('certified','certificate_reference','certificate_delivery_date'):
                    if name in self.fields:
                        self.fields[name].disabled=True
                        self.fields[name].help_text='Utilize «Registar estados documentais» para preservar a evidência e auditoria individual.'
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

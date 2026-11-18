from decimal import Decimal

from django import forms
from .planning import check_submitted_total


class DocumentaryForm(forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    justification = forms.CharField(label='Justificação obrigatória', max_length=4000,
        widget=forms.Textarea(attrs={'rows':3}))
    reference = forms.CharField(label='Referência documental obrigatória', max_length=1000)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'gf-field'

    def clean(self):
        data = super().clean()
        allowed = set(self.fields) | {'csrfmiddlewaretoken', 'action', 'total_hours'}
        if set(self.data) - allowed:
            raise forms.ValidationError('O pedido contém campos não autorizados.')
        if self.is_bound and any(len(self.data.getlist(k)) != 1 for k in self.data if hasattr(self.data, 'getlist')):
            raise forms.ValidationError('O pedido contém valores repetidos.')
        return data


class CorrectionForm(DocumentaryForm):
    correction_type = forms.ChoiceField(label='Tipo de correcção', choices=[
        ('documented_standard','Decisão documentada sobre o padrão')])
    days = forms.IntegerField(label='Dias previstos propostos', min_value=1, max_value=366)
    hours = forms.DecimalField(label='Horas de formação por dia propostas',
        min_value=Decimal('.5'), max_value=24, max_digits=4, decimal_places=2)

    @property
    def total_hours(self):
        data = getattr(self, 'cleaned_data', {}) if self.is_bound else self.initial
        days, hours = data.get('days'), data.get('hours')
        return Decimal(days) * Decimal(hours) if days is not None and hours is not None else None

    def clean(self):
        data = super().clean()
        check_submitted_total(self, self.total_hours)
        return data


class ConfirmationForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(label='Confirmo expressamente esta operação e a preservação do histórico.')

    def clean(self):
        data = super().clean()
        if set(self.data) - {'token','confirmed','action','csrfmiddlewaretoken'}:
            raise forms.ValidationError('A confirmação contém campos não autorizados.')
        return data

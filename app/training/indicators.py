"""Explicit indicator denominators with participation-level reconciliation."""
from decimal import Decimal
from django import forms
from .widgets import date_widgets
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import Employee, Training, ReactionEvaluation
from .analytics import statistics
from .kpi_service import Scope
from .competencies import managed_cycles
from .competency_services import cycle_results
from .views import staff_required


class IndicatorFilter(forms.Form):
    employee = forms.ModelChoiceField(label='Trabalhador', queryset=Employee.objects.all(), required=False)
    start = forms.DateField(label='Início do período', required=False, widget=forms.DateInput(attrs={'type':'date'}))
    end = forms.DateField(label='Fim do período', required=False, widget=forms.DateInput(attrs={'type':'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        date_widgets(self.fields)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'gf-field'

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['end'] < data['start']:
            raise forms.ValidationError('O fim não pode anteceder o início.')
        return data


def mean(values):
    return sum(values, Decimal(0)) / len(values) if values else None


def analyse_indicators(rows, scope=None, employees=None):
    rows = list(rows)
    shared = statistics(rows, employees, scope=scope)
    reconciliation = []
    presences = days = 0
    grades, outcomes = [], []
    satisfaction = {key:[] for key in ['organization','content','trainer','usefulness']}
    logistics = []
    for row in rows:
        attendance = row.pk in shared['kpi_summary'].attendance_pairs
        evaluation = getattr(row, 'evaluation', None)
        final_valid = bool(evaluation and evaluation.validation == 'Validado' and
            evaluation.validated_grade == row.grade and evaluation.validated_result == row.result and row.status == 'Concluído')
        learning = final_valid and row.grade is not None
        reaction = getattr(row, 'reaction', None)
        reaction_valid = bool(reaction and reaction.validation == 'Validado')
        if attendance:
            presences += row.attendance
            days += row.days or 0
        if learning:
            grades.append(row.grade)
        if final_valid and row.result in ('Aprovado','Reprovado'):
            outcomes.append(row.result)
        if evaluation and evaluation.validation == 'Validado' and evaluation.logistics is not None:
            logistics.append(evaluation.logistics)
        for field in satisfaction:
            value = getattr(reaction,field,None) if reaction_valid else None
            if value is not None:
                satisfaction[field].append(Decimal(value))
        reconciliation.append({'training':row, 'attendance':attendance, 'learning':learning,
            'reaction':reaction_valid, 'final_valid':final_valid,
            'reaction_count':sum(getattr(reaction,f,None) is not None for f in satisfaction) if reaction_valid else 0})
    academic = shared['outcomes']
    certificates = {
        'issued':sum(r.certificate_issued is True for r in rows),
        'issued_unknown':sum(r.certificate_issued is None for r in rows),
        'participant':sum(r.certified is True for r in rows),
        'participant_unknown':sum(r.certified is None for r in rows),
        'hr':sum(r.certificate_hr_delivered is True for r in rows),
        'hr_unknown':sum(r.certificate_hr_delivered is None for r in rows),
        'declarations':sum(bool(r.certificate_declaration) for r in rows),
    }
    return {'stats':shared, 'total':len(rows), 'reconciliation':reconciliation,
        'attendance': shared['attendance'], 'kpi_summary': shared['kpi_summary'], 'kpi_details': shared['kpi_details'], 'presences':presences,'days':days,
        'attendance_count':sum(r['attendance'] for r in reconciliation),
        'attendance_provisional':sum(not r['attendance'] for r in reconciliation),
        'outcomes':academic, 'learning':academic['learning'],'learning_count':academic['learning_count'],'learning_provisional':len(rows)-academic['learning_count'],
        'approved':academic['approved'],'result_count':academic['result_count'],
        'approval_rate':academic['approval_rate'],
        'logistics':academic['logistics'],'logistics_count':academic['logistics_count'], 'logistics_percent':academic['logistics_percent'],
        'satisfaction':[{'label':dict(organization='Organização', content='Conteúdo', trainer='Formador', usefulness='Utilidade')[k],
                         'mean':mean(v),'count':len(v),'excluded':len(rows)-len(v)} for k,v in satisfaction.items()],
        'certificates':certificates}


@login_required
@staff_required
def index(request):
    form = IndicatorFilter(request.GET)
    rows = Training.objects.select_related('employee','group','evaluation','reaction').all()
    cycles = managed_cycles(request.user)
    if form.is_valid():
        data = form.cleaned_data
        if data.get('employee'):
            rows = rows.filter(employee=data['employee'])
            cycles = cycles.filter(employee=data['employee'])
        if data.get('start'):
            rows = rows.filter(end__gte=data['start'])
            cycles = cycles.filter(start__gte=data['start'])
        if data.get('end'):
            rows = rows.filter(start__lte=data['end'])
            cycles = cycles.filter(start__lte=data['end'])
    else:
        rows, cycles = rows.none(), cycles.none()
    reference = Employee.objects.all()
    if form.is_valid() and form.cleaned_data.get('employee'):
        reference = reference.filter(pk=form.cleaned_data['employee'].pk)
    summary = analyse_indicators(rows, Scope(form.cleaned_data if form.is_valid() else {}, 'Período de início seleccionado', 'session_date', 'current_registry', form.cleaned_data.get('start'), form.cleaned_data.get('end')), reference)
    competency_rows = [{'cycle':c, 'result':cycle_results(c)} for c in cycles]
    usable = [r['result']['overall'] for r in competency_rows if r['result']['available']]
    summary.update(form=form, competency_rows=competency_rows, competency_average=mean(usable),
        competency_count=len(usable), competency_provisional=len(competency_rows)-len(usable))
    return render(request,'competencies/indicators.html',summary,status=200 if form.is_valid() else 400)

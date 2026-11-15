from .kpi_catalogue import export_rows as catalogue_rows
from dataclasses import replace
from .certificates import export_rows as certificate_rows
"""Executive indicators derived from the same participation records as the dossiers."""
from collections import Counter, defaultdict
from decimal import Decimal
import csv
from django import forms
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from .analytics import statistics
from .kpi_service import Scope
from .kpi_reporting import STATUS, export_rows, outcome_rows
from .batch import EDITION_FIELDS
from .models import Employee, Training


class DashboardFilters(forms.Form):
    year = forms.ChoiceField(label='Ano de início', required=False)
    direction = forms.ChoiceField(label='Direcção', required=False)
    area = forms.ChoiceField(label='Área de formação', required=False)
    status = forms.ChoiceField(label='Situação', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['year'].choices = [('', 'Todos os anos')] + [(str(y), str(y)) for date in Training.objects.dates('start', 'year', order='DESC') for y in [date.year]]
        self.fields['direction'].choices = [('', 'Todas as direcções')] + [(x, x) for x in Employee.objects.exclude(direction='').values_list('direction', flat=True).distinct().order_by('direction')]
        self.fields['area'].choices = [('', 'Todas as áreas')] + [(x, x) for x in Training.objects.exclude(area='').values_list('area', flat=True).distinct().order_by('area')]
        self.fields['status'].choices = [('', 'Todas as situações')] + list(Training._meta.get_field('status').choices)


def bars(mapping):
    peak = max((v for v in mapping.values() if v is not None), default=0)
    return [{'label': k, 'value': v, 'width': round(float(v / peak * 100), 2) if peak and v is not None else 0}
            for k, v in mapping.items()]


def executive_data(rows, employees, year='', scope=None):
    rows, employees = list(rows), list(employees)
    scope = scope or Scope({'year': year}, year or 'Todos os períodos', 'training_start', 'current_registry')
    stats = statistics(rows, employees, scope)
    summary = stats['kpi_summary']
    present = {r.employee_id for r in rows if r.pk in summary.effective_participations}
    editions = {tuple(getattr(r, f) for f in EDITION_FIELDS) for r in rows}
    academic = stats['outcomes']
    results = Counter(academic['results'].values())
    evaluated = results['Aprovado'] + results['Reprovado']
    final_grades = [r.grade for r in rows if r.pk in academic['results'] and r.grade is not None]
    areas, timeline = defaultdict(Decimal), defaultdict(Decimal)
    if year:
        months = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        timeline.update({x: Decimal(0) for x in months})
    else:
        timeline.update({str(y): Decimal(0) for y in sorted({r.start.year for r in rows})})
    for row in rows:
        if row.pk not in summary.hours_by_participation:
            continue
        areas[row.area or 'Área por confirmar'] += summary.hours_by_participation[row.pk]
        timeline[months[row.start.month - 1] if year else str(row.start.year)] += summary.hours_by_participation[row.pk]
    measured_periods = {months[r.start.month - 1] if year else str(r.start.year)
                        for r in rows if r.pk in summary.hours_by_participation}
    timeline = {key: value if key in measured_periods else None for key, value in timeline.items()}
    if scope.time_basis == 'session_date':
        timeline = {x: None for x in months} if year else {}
        for day, value in summary.dated_hours.items():
            key = months[int(day[5:7])-1] if year else day[:4]
            timeline[key] = (timeline.get(key) or Decimal(0)) + value
    by_direction = {}
    for person in employees:
        label = person.direction or 'Direcção por confirmar'
        item = by_direction.setdefault(label, {'total': 0, 'present': set(), 'hours': Decimal(0), 'certificates': 0})
        item['total'] += 1
    for row in rows:
        label = row.employee.direction or 'Direcção por confirmar'
        item = by_direction.setdefault(label, {'total': 0, 'present': set(), 'hours': Decimal(0), 'certificates': 0})
        if row.employee_id in present:
            item['present'].add(row.employee_id)
        item['hours'] += summary.hours_by_participation.get(row.pk, Decimal(0))
        item['certificates'] += row.certified is True
    directions = []
    for label, x in sorted(by_direction.items()):
        subset = [r for r in rows if (r.employee.direction or 'Direcção por confirmar') == label]
        reference = [e for e in employees if (e.direction or 'Direcção por confirmar') == label]
        ds = statistics(subset, reference, replace(scope,filters={**scope.filters,'reference':None},reference='current_registry'))
        directions.append(dict(label=label, total=x['total'], present=ds['confirmed_people'],
            coverage=ds['kpi_summary'].metrics['coverage'].value, hours=ds['hours'], certificates=x['certificates'],
            evidence=ds['kpi_details'], coverage_status=STATUS[ds['kpi_summary'].metrics['coverage'].status]))
    cards = [
        ('Técnicos na base', len(employees), 'Na direcção seleccionada; inclui técnicos sem formação', 'groups'),
        ('Técnicos com presença', len(present), 'Pessoas distintas com pelo menos uma presença verificada', 'person_check'),
        ('Turmas', len(editions), 'Edições distintas nos filtros seleccionados', 'school'),
        ('Participações efectivas', stats['effective_participations'], 'Participações distintas com presença positiva verificada', 'how_to_reg'),
        ('Horas realizadas', stats['hours'], 'Horas-participante verificadas; duração uniforme por participação', 'schedule'),
        ('Assiduidade', stats['attendance'], 'Horas verificadas ÷ horas previstas correspondentes; inclui ausências verificadas', 'speed'),
        ('Certificados entregues', stats['certificates'], 'Somente entregas confirmadas', 'workspace_premium'),
        ('Aproveitamento', academic['approval_rate'],
         'Aprovados ÷ resultados determinados e validados; coorte terminada', 'verified'),
    ]
    kpis = [{'label': label, 'value': value, 'note': note, 'icon': icon,
             'decimal': i in (4, 5, 7), 'suffix': 'h' if i == 4 else '%' if i in (5, 7) else ''}
            for i, (label, value, note, icon) in enumerate(cards)]
    for index, key in {1:'people', 3:'participations', 4:'hours', 5:'attendance'}.items():
        kpis[index]['evidence_status'] = STATUS[summary.metrics[key].status]
    return {'kpi_summary': summary, 'kpi_details': stats['kpi_details'], 'stats': stats, 'kpis': kpis, 'directions_table': directions,
        'coverage': summary.metrics['coverage'].value,
        'average_final': academic['learning'],
        'final_grades_count': academic['learning_count'], 'evaluated': evaluated,
        'results': [{'label': x, 'value': results[x]} for x in ('Aprovado', 'Reprovado', 'Pendente', 'Não avaliado')],
        'status_bars': bars({x: sum(r.status == x for r in rows) for x, _ in Training._meta.get_field('status').choices}),
        'area_bars': bars(dict(sorted(areas.items(), key=lambda x: (-x[1], x[0])))),
        'timeline_bars': bars(timeline), 'timeline_title': ('Horas por mês de sessão' if year else 'Horas por ano de sessão') if scope.time_basis=='session_date' else ('Horas por mês de início' if year else 'Horas por ano de início'),
        'has_rows': bool(rows)}


@login_required
def dashboard(request):
    from .executive import selection, analyse
    form, selected, start, end, label, rows, employees, base, valid = selection(request)
    data = analyse(rows, employees, selected, start, end, label, base)
    data.update(filter_form=form, export_query=data['query'])
    if not (request.user.is_staff or request.user.has_perms(['training.view_employee','training.view_training'])):
        data['talents']=[];data['scatter']=[]
    if data.get('privacy_restricted'):
        from .aggregate_privacy import export, page
        return export('csv') if request.GET.get('export')=='csv' else page(request,data)
    if request.GET.get('export') == 'csv' and valid:
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="EPAL_Dashboard_Executivo.csv"'
        response.write('\ufeff')
        raw_writer = csv.writer(response, delimiter=';')
        class SafeWriter:
            def writerow(self, values):
                raw_writer.writerow(["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else v for v in values])
        writer = SafeWriter()
        writer.writerow(['Dashboard Executivo EPAL — Base provisória'])
        writer.writerow(['Gerado em', data['generated_at'].strftime('%d/%m/%Y %H:%M')])
        for key, value in selected.items():
            writer.writerow([form.fields[key].label, value or 'Todos'])
        writer.writerow(['Indicador', 'Valor', 'Critério', 'Estado'])
        for item in data['kpis']:
            writer.writerow([item['label'], item['value'] if item['value'] is not None else 'Sem dados', item['note'], item.get('evidence_status', '')])
        writer.writerow([])
        writer.writerow(['Direcção', 'Técnicos na base', 'Técnicos com presença', 'Cobertura (%)', 'Horas de participantes', 'Certificados entregues'])
        for item in data['directions_table']:
            label = item['label']
            # Spreadsheet applications must not interpret a source label as a formula.
            if label.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
                label = "'" + label
            writer.writerow([label, item['total'], item['present'], item['coverage'], item['hours'], item['certificates']])
        writer.writerow([])
        for row in export_rows(data['kpi_summary']) + outcome_rows(data['stats']['outcomes']) + certificate_rows(data['stats']['certification']) + catalogue_rows(data['stats']):
            writer.writerow(row)
        return response
    return render(request, 'dashboard.html', data)

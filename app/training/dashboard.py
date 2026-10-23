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
    peak = max(mapping.values(), default=0)
    return [{'label': k, 'value': v, 'width': round(float(v / peak * 100), 2) if peak else 0}
            for k, v in mapping.items()]


def executive_data(rows, employees, year=''):
    rows, employees = list(rows), list(employees)
    stats = statistics(rows)
    present = {r.employee_id for r in rows if r.status != 'Planeado' and r.attendance > 0}
    editions = {tuple(getattr(r, f) for f in EDITION_FIELDS) for r in rows}
    results = Counter(r.result for r in rows if r.status == 'Concluído')
    evaluated = results['Aprovado'] + results['Reprovado']
    final_grades = [r.grade for r in rows if r.status == 'Concluído' and r.grade is not None]
    areas, timeline = defaultdict(Decimal), defaultdict(Decimal)
    if year:
        months = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        timeline.update({x: Decimal(0) for x in months})
    else:
        timeline.update({str(y): Decimal(0) for y in sorted({r.start.year for r in rows})})
    for row in rows:
        areas[row.area or 'Área por confirmar'] += (row.completed_hours or Decimal(0))
        timeline[months[row.start.month - 1] if year else str(row.start.year)] += (row.completed_hours or Decimal(0))
    by_direction = {}
    for person in employees:
        label = person.direction or 'Direcção por confirmar'
        item = by_direction.setdefault(label, {'total': 0, 'present': set(), 'hours': Decimal(0), 'certificates': 0})
        item['total'] += 1
    for row in rows:
        label = row.employee.direction or 'Direcção por confirmar'
        item = by_direction[label]
        if row.employee_id in present:
            item['present'].add(row.employee_id)
        item['hours'] += (row.completed_hours or Decimal(0))
        item['certificates'] += row.certified is True
    directions = [{'label': label, 'total': x['total'], 'present': len(x['present']),
                   'coverage': len(x['present']) * 100 / x['total'], 'hours': x['hours'], 'certificates': x['certificates']}
                  for label, x in sorted(by_direction.items())]
    cards = [
        ('Técnicos na base', len(employees), 'Na direcção seleccionada; inclui técnicos sem formação', 'groups'),
        ('Técnicos com presença', len(present), 'Pessoas distintas com pelo menos uma presença', 'person_check'),
        ('Turmas', len(editions), 'Edições distintas nos filtros seleccionados', 'school'),
        ('Participações', len(rows), 'Uma inscrição por técnico e formação', 'how_to_reg'),
        ('Horas realizadas', stats['hours'], 'Soma das horas de presença de todos os participantes', 'schedule'),
        ('Assiduidade', stats['attendance'], 'Presenças ÷ dias previstos; exclui formações planeadas', 'speed'),
        ('Certificados entregues', stats['certificates'], 'Somente entregas confirmadas', 'workspace_premium'),
        ('Aproveitamento', Decimal(results['Aprovado']) * 100 / evaluated if evaluated else None,
         'Aprovados ÷ (aprovados + reprovados), em participações concluídas', 'verified'),
    ]
    kpis = [{'label': label, 'value': value, 'note': note, 'icon': icon,
             'decimal': i in (4, 5, 7), 'suffix': 'h' if i == 4 else '%' if i in (5, 7) else ''}
            for i, (label, value, note, icon) in enumerate(cards)]
    return {'stats': stats, 'kpis': kpis, 'directions_table': directions,
        'coverage': len(present) * 100 / len(employees) if employees else None,
        'average_final': sum(final_grades) / len(final_grades) if final_grades else None,
        'final_grades_count': len(final_grades), 'evaluated': evaluated,
        'results': [{'label': x, 'value': results[x]} for x in ('Aprovado', 'Reprovado', 'Pendente', 'Não avaliado')],
        'status_bars': bars({x: sum(r.status == x for r in rows) for x, _ in Training._meta.get_field('status').choices}),
        'area_bars': bars(dict(sorted(areas.items(), key=lambda x: (-x[1], x[0])))),
        'timeline_bars': bars(timeline), 'timeline_title': 'Horas por mês de início' if year else 'Horas por ano de início',
        'has_rows': bool(rows)}


@login_required
def dashboard(request):
    from .executive import selection, analyse
    form, selected, start, end, label, rows, employees, base, valid = selection(request)
    data = analyse(rows, employees, selected, start, end, label, base)
    data.update(filter_form=form, export_query=data['query'])
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
        writer.writerow(['Indicador', 'Valor', 'Critério'])
        for item in data['kpis']:
            writer.writerow([item['label'], item['value'] if item['value'] is not None else 'Sem dados', item['note']])
        writer.writerow([])
        writer.writerow(['Direcção', 'Técnicos na base', 'Técnicos com presença', 'Cobertura (%)', 'Horas de participantes', 'Certificados entregues'])
        for item in data['directions_table']:
            label = item['label']
            # Spreadsheet applications must not interpret a source label as a formula.
            if label.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
                label = "'" + label
            writer.writerow([label, item['total'], item['present'], item['coverage'], item['hours'], item['certificates']])
        return response
    return render(request, 'dashboard.html', data)

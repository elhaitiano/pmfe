"""Read-only participation report using the current registry and verified attendance."""
from collections import defaultdict
from io import BytesIO
from xml.sax.saxutils import escape
from django import forms
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from .models import Course, Employee, Training

STATES = [('', 'Todos'), ('participated', 'Com presença confirmada'),
          ('absent', 'Sem participação registada'), ('completed', 'Conclusão confirmada'),
          ('no_training', 'Sem formação registada')]

class Filters(forms.Form):
    course = forms.ModelChoiceField(label='Formação do catálogo', queryset=Course.objects.all(), required=False)
    catalogue_search = forms.CharField(label='Pesquisar formação (inclui histórico)', required=False, max_length=200)
    directions = forms.MultipleChoiceField(label='Direcções actuais', required=False)
    year = forms.IntegerField(label='Ano de início', required=False, min_value=1900, max_value=9999)
    date_from = forms.DateField(label='De', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    date_to = forms.DateField(label='Até', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    state = forms.ChoiceField(label='Situação da lista nominal', choices=STATES, required=False)
    q = forms.CharField(label='Nome ou número mecanográfico', required=False, max_length=200)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['directions'].choices = [(x, x or 'Direcção por confirmar') for x in Employee.objects.values_list('direction', flat=True).distinct().order_by('direction')]

    def clean(self):
        data = super().clean()
        if data.get('date_from') and data.get('date_to') and data['date_from'] > data['date_to']:
            self.add_error('date_to', 'A data final não pode anteceder a inicial.')
        return data


def analyse(data):
    employees = Employee.objects.all().order_by('name', 'pk')
    if data.get('directions'):
        employees = employees.filter(direction__in=data['directions'])
    rows = Training.objects.filter(employee__in=employees).select_related('group')
    if data.get('course'):
        rows = rows.filter(course=data['course'])
    if data.get('catalogue_search'):
        rows = rows.filter(title__icontains=data['catalogue_search'])
    if data.get('year'):
        rows = rows.filter(start__year=data['year'])
    if data.get('date_from'):
        rows = rows.filter(end__gte=data['date_from'])
    if data.get('date_to'):
        rows = rows.filter(start__lte=data['date_to'])
    grouped = defaultdict(list)
    rows = list(rows.order_by('start', 'pk'))
    for row in rows:
        grouped[row.employee_id].append(row)
    nominal, directions = [], {}
    for employee in employees:
        history = grouped[employee.pk]
        present = any(r.status != 'Planeado' and r.attendance_confirmed and r.attendance > 0 for r in history)
        complete = any(r.status == 'Concluído' and r.attendance_confirmed and r.attendance > 0 for r in history)
        label = employee.direction or 'Direcção por confirmar'
        summary = directions.setdefault(label, dict(direction=label, total=0, participated=0, completed=0))
        summary['total'] += 1
        summary['participated'] += present
        summary['completed'] += complete
        nominal.append(dict(employee=employee, number=employee.number, name=employee.name,
            direction=label, category=employee.category, registry=employee.registration_validation,
            participated=present, completed=complete, no_training=not history,
            status='Conclusão confirmada' if complete else 'Com presença confirmada' if present else 'Sem participação registada',
            editions=[dict(title=r.title, group=str(r.group) if r.group else 'Grupo não associado',
                start=r.start, end=r.end, hours=r.completed_hours if r.attendance_confirmed else None,
                attendance=r.attendance_percent if r.attendance_confirmed else None,
                grades=[dict(value=r.grade, scale=20)] if r.grade is not None else []) for r in history]))
    for row in directions.values():
        row['not_participated'] = row['total'] - row['participated']
        row['rate'] = round(row['participated'] * 100 / row['total'], 1)
        row['tone'] = 'high' if row['rate'] >= 75 else 'medium' if row['rate'] >= 40 else 'low'
    universe = len(nominal)
    participated = sum(r['participated'] for r in nominal)
    return dict(universe=universe, participated=participated, not_participated=universe-participated,
        completed=sum(r['completed'] for r in nominal), rate=round(participated*100/universe, 1) if universe else None,
        course_label=str(data.get('course') or 'Todas as formações'),
        denominator='Universo: funcionários cadastrados nas direcções seleccionadas.',
        historical_direction='A direcção é a actual, não a direcção histórica da formação.',
        participations=len(rows), distinct_courses=len({('course', r.course_id) if r.course_id else ('title', r.title.casefold()) for r in rows}),
        operational_summary='Conclusão confirmada: estado Concluído e pelo menos uma presença verificada. Não implica aprovação nem certificado.'), list(directions.values()), nominal


def selected_people(rows, state='', query='', direction=None):
    if direction is not None:
        rows = [r for r in rows if r['direction'] == direction]
    if state == 'absent':
        rows = [r for r in rows if not r['participated']]
    elif state in ('participated', 'completed', 'no_training'):
        rows = [r for r in rows if r[state]]
    if query:
        rows = [r for r in rows if query.casefold() in (r['name']+' '+r['number']).casefold()]
    return rows


def export_report(kind, report, directions, nominal):
    headers = ['Direcção', 'Técnicos', 'Com presença', 'Sem participação', 'Conclusão confirmada', 'Taxa (%)']
    table = [[r[k] for k in ('direction', 'total', 'participated', 'not_participated', 'completed', 'rate')] for r in directions]
    stream = BytesIO()
    if kind == 'xlsx':
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.title = 'Direcções'
        def append(ws, row):
            ws.append(row)
            for cell in ws[ws.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        append(sheet, headers)
        for row in table:
            append(sheet, row)
        people = book.create_sheet('Lista nominal')
        append(people, ['N.º mec.', 'Nome', 'Direcção actual', 'Categoria', 'Situação'])
        for row in nominal:
            append(people, [row[k] for k in ('number', 'name', 'direction', 'category', 'status')])
        for ws in book:
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(60, max(18, max(len(str(c.value or '')) for c in col)+2))
        book.save(stream)
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        styles = getSampleStyleSheet()
        para = lambda v: Paragraph(escape(str(v)), styles['BodyText'])
        content = [Paragraph('Participação por Direcção', styles['Title']), para(report['course_label']),
            para(f"Universo: {report['universe']} · Com presença: {report['participated']} · Taxa: {report['rate'] if report['rate'] is not None else '—'}%"),
            para(report['denominator']+' '+report['historical_direction']), para(report['operational_summary']), Spacer(1, 12)]
        t = Table([[para(v) for v in row] for row in [headers]+table], repeatRows=1, colWidths=[235, 65, 85, 95, 115, 65])
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dceef2')), ('VALIGN',(0,0),(-1,-1),'TOP'), ('GRID',(0,0),(-1,-1),.4,colors.lightgrey)]))
        content.append(t)
        SimpleDocTemplate(stream, pagesize=landscape(A4), rightMargin=30, leftMargin=30).build(content)
        mime = 'application/pdf'
    response = HttpResponse(stream.getvalue(), content_type=mime)
    response['Content-Disposition'] = f'attachment; filename="participacao-direccao.{kind}"'
    return response


@login_required
@permission_required(['training.view_employee', 'training.view_training'], raise_exception=True)
@require_GET
def index(request):
    form = Filters(request.GET)
    if not form.is_valid():
        return render(request, 'participation_executive.html', {'form': form}, status=400)
    report, directions, nominal = analyse(form.cleaned_data)
    if request.GET.get('export') in ('xlsx', 'pdf'):
        return export_report(request.GET['export'], report, directions, selected_people(nominal, form.cleaned_data.get('state'), form.cleaned_data.get('q')))
    def url(**changes):
        params = request.GET.copy()
        params.pop('export', None)
        for key, value in changes.items():
            if value is None:
                params.pop(key, None)
            else:
                params[key] = str(value)
        return reverse('participation_executive')+'?'+params.urlencode()
    for row in directions:
        for key, state in [('detail_url',''), ('participated_url','participated'), ('absent_url','absent'), ('completed_url','completed')]:
            row[key] = url(detail_direction=row['direction'], detail_state=state, page=None)
    query = request.GET.get('direction_q', '')[:200]
    filtered = [r for r in directions if query.casefold() in r['direction'].casefold()]
    sort = request.GET.get('sort', 'direction')
    keys = ['direction','total','participated','not_participated','completed','rate']
    if sort.lstrip('-') not in keys:
        sort = 'direction'
    filtered.sort(key=lambda r: r[sort.lstrip('-')].casefold() if sort.lstrip('-') == 'direction' else r[sort.lstrip('-')], reverse=sort.startswith('-'))
    size = request.GET.get('direction_size', '10')
    size = int(size) if size in ('10','20','50') else 10
    direction_page = Paginator(filtered, size).get_page(request.GET.get('direction_page'))
    headers = []
    for key, label in zip(keys, ['Direcção','Técnicos','Já participaram','Sem participação','Conclusão confirmada','Taxa']):
        active = sort.lstrip('-') == key
        headers.append(dict(label=label, active=active, aria_sort=('descending' if sort.startswith('-') else 'ascending') if active else 'none', url=url(sort='-'+key if sort == key else key, direction_page=None)))
    detail_state = request.GET.get('detail_state', form.cleaned_data.get('state', ''))
    if detail_state not in dict(STATES):
        detail_state = ''
    detail_direction = request.GET.get('detail_direction')
    people = selected_people(nominal, detail_state, form.cleaned_data.get('q'), detail_direction)
    page = Paginator(people, 25).get_page(request.GET.get('page'))
    context = dict(form=form, report=report, direction_page=direction_page, direction_size=size, direction_q=query,
        direction_headers=headers, direction_pages=[dict(number=n, current=n == direction_page.number, url=url(direction_page=n) if isinstance(n,int) else None) for n in direction_page.paginator.get_elided_page_range(direction_page.number)],
        page=page, detail_title=detail_direction or 'Todas as direcções', detail_state_label=dict(STATES)[detail_state],
        open_nominal='detail_state' in request.GET or detail_direction is not None or bool(form.cleaned_data.get('q') or form.cleaned_data.get('state')),
        nominal_previous=url(page=page.previous_page_number()) if page.has_previous() else None,
        nominal_next=url(page=page.next_page_number()) if page.has_next() else None,
        excel_url=url(export='xlsx'), pdf_url=url(export='pdf'))
    for key,state in [('nominal_url',''),('participants_url','participated'),('absent_url','absent'),('no_training_url','no_training')]:
        context[key] = url(detail_state=state, detail_direction=None, page=None)
    return render(request, 'participation_executive.html', context)

"""Small, audited score grid. No attachment or certificate transition."""
import json
from io import BytesIO
from decimal import Decimal
from django import forms
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Course, TrainingGroup, Enrollment, AssessmentScale, DirectAssessment, AuditEvent
from .views import staff_required
from .direct_assessment_metrics import normalise, mention, metrics

VIEW_PERMS = ('training.view_employee', 'training.view_training', 'training.view_traininggroup')
FIELDS = ('initial_points', 'final_points', 'satisfaction')


class Scores(forms.Form):
    initial_points = forms.DecimalField(required=False, min_value=0, max_digits=8, decimal_places=2)
    final_points = forms.DecimalField(required=False, min_value=0, max_digits=8, decimal_places=2)
    satisfaction = forms.TypedChoiceField(required=False, coerce=int, empty_value=None,
        choices=[('', 'Sem resposta')] + [(n, str(n)) for n in range(1, 6)])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values(): field.widget.attrs['class'] = 'gf-field'


class ScaleForm(forms.Form):
    initial_max = forms.DecimalField(label='Máximo inicial (pontos)', min_value=Decimal('.01'), max_digits=8, decimal_places=2, initial=100)
    final_max = forms.DecimalField(label='Máximo final (pontos)', min_value=Decimal('.01'), max_digits=8, decimal_places=2, initial=100)
    reason = forms.CharField(label='Motivo da correcção (apenas ao substituir valores)', required=False, max_length=1000)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values(): field.widget.attrs['class'] = 'gf-field'


def roster(group):
    return Enrollment.objects.filter(group=group, state='Activa').exclude(confirmation='Não participa').select_related('employee', 'direct_assessment')


def scope_rows(enrollments):
    rows = []
    for e in enrollments:
        assessment = getattr(e, 'direct_assessment', None)
        scale = getattr(e.group, 'assessment_scale', None)
        initial = normalise(assessment.initial_points, scale.initial_max) if assessment and scale else None
        final = normalise(assessment.final_points, scale.final_max) if assessment and scale else None
        rows.append(dict(enrollment=e, initial=initial, final=final,
            evolution=final-initial if final is not None and initial is not None else None,
            mention=mention(final), satisfaction=assessment.satisfaction if assessment else None))
    return rows


def snapshot(group):
    scale = getattr(group, 'assessment_scale', None)
    return dict(group=group.pk, state=group.state, scale=[str(scale.initial_max), str(scale.final_max), scale.revision] if scale else None,
        rows=[[e.pk, e.employee_id, str(getattr(e, 'direct_assessment', None).updated_at) if hasattr(e, 'direct_assessment') else None] for e in roster(group)])


def excel_text(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def export(rows, summary, course, group, kind):
    title = f'{course.title} · {group.code if group else "Todos os grupos"}'
    stamp = timezone.localtime().strftime('%d/%m/%Y | %H:%M')
    headings = ['N.º mec.', 'Formando', 'Grupo', 'Inicial /20', 'Final /20', 'Evolução /20', 'Menção', 'Satisfação /5']
    values = [[r['enrollment'].employee.number, r['enrollment'].employee.name, r['enrollment'].group.code,
        r['initial'], r['final'], r['evolution'], r['mention'], r['satisfaction']] for r in rows]
    labels = [('Participações', 'total'), ('Pares completos', 'pairs'), ('Média inicial (pares) /20', 'initial'),
        ('Média final (pares) /20', 'paired_final'), ('Evolução /20', 'evolution'), ('Média final /20', 'final'),
        ('Testes finais', 'final_count'), ('Satisfação /5', 'satisfaction'), ('Respostas', 'responses')]
    if kind == 'xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        book = Workbook(); sheet = book.active; sheet.title = 'Resumo'
        sheet.append([excel_text(title)]); sheet.append([stamp])
        for label, key in labels: sheet.append([label, summary[key]])
        sheet.append(['Menções', 'Regra da plataforma; não determina certificados.'])
        sheet = book.create_sheet('Resultados'); sheet.append(headings)
        for row in values: sheet.append([excel_text(v) if isinstance(v, str) else v for v in row])
        for page in book:
            page.freeze_panes = 'A2'
            for cell in page[1]: cell.font = Font(color='FFFFFF', bold=True); cell.fill = PatternFill('solid', fgColor='073B75')
            for column in page.columns:
                page.column_dimensions[column[0].column_letter].width = min(60, max(16, max(len(str(c.value or '')) for c in column)+2))
        sheet.auto_filter.ref = sheet.dimensions
        out = BytesIO(); book.save(out)
        response = HttpResponse(out.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    else:
        from html import escape
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.pagesizes import A4, landscape
        styles = getSampleStyleSheet(); out = BytesIO()
        fmt = lambda v: '—' if v is None else f'{v:.2f}' if isinstance(v, Decimal) else str(v)
        story = [Paragraph(escape(title), styles['Heading2']), Paragraph(stamp, styles['Normal']), Spacer(1, 12)]
        for label, key in labels: story.append(Paragraph(escape(f'{label}: {fmt(summary[key])}'), styles['Normal']))
        story.append(Spacer(1, 12))
        data = [[Paragraph(escape(str(c)), styles['Normal']) for c in headings]]
        data += [[Paragraph(escape(fmt(c)), styles['Normal']) for c in row] for row in values]
        table = Table(data, repeatRows=1, colWidths=[58, 160, 45, 70, 70, 75, 110, 90])
        table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), '#E8F1FA'), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), .3, '#CBD5E1')]))
        story.append(table)
        def footer(canvas, doc):
            canvas.setFont('Helvetica', 8); canvas.drawRightString(800, 20, f'Pág. {doc.page}')
        SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=25, rightMargin=25).build(story, onFirstPage=footer, onLaterPages=footer)
        response = HttpResponse(out.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="avaliacao-formacao-{course.pk}.{kind}"'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@staff_required
@require_http_methods(['GET', 'POST'])
def index(request):
    if not request.user.has_perms(VIEW_PERMS): raise PermissionDenied
    if any(value and (not value.isascii() or not value.isdigit() or len(value) > 18) for value in (request.GET.get('course'), request.GET.get('group'))):
        return HttpResponse('Selecção inválida.', status=400)
    group = get_object_or_404(TrainingGroup.objects.select_related('course', 'assessment_scale'), pk=request.GET['group']) if request.GET.get('group') else None
    course = get_object_or_404(Course, pk=request.GET['course']) if request.GET.get('course') else (group.course if group else None)
    if group and group.course_id != course.pk: return HttpResponse('Grupo e formação incompatíveis.', status=400)
    editable = bool(group and group.state != 'Cancelada' and request.user.has_perm('training.change_training'))
    enrollments = Enrollment.objects.filter(group__course=course, state='Activa').exclude(confirmation='Não participa').select_related('employee', 'group__assessment_scale', 'direct_assessment') if course else Enrollment.objects.none()
    enrollments = enrollments.filter(group=group) if group else enrollments.exclude(group__state='Cancelada')
    rows = scope_rows(enrollments); summary = metrics([] if group and group.state == 'Cancelada' else rows)
    if request.method == 'GET' and request.GET.get('export') in ('xlsx', 'pdf') and course:
        return export(rows, summary, course, group, request.GET['export'])
    page = Paginator(rows, 30).get_page(request.GET.get('page'))
    scale = getattr(group, 'assessment_scale', None) if group else None
    scale_form = ScaleForm(request.POST if request.method == 'POST' else None,
        initial={'initial_max': scale.initial_max if scale else 100, 'final_max': scale.final_max if scale else 100})
    for row in page:
        a = getattr(row['enrollment'], 'direct_assessment', None)
        row['form'] = Scores(request.POST if request.method == 'POST' else None, prefix=str(row['enrollment'].pk),
            initial={f: getattr(a, f) if a else None for f in FIELDS})
    error = None
    if request.method == 'POST':
        if not editable: raise PermissionDenied
        try:
            token = signing.loads(request.POST.get('token', ''), salt='direct-assessment', max_age=7200)
            if token['user'] != request.user.pk or token['ids'] != [r['enrollment'].pk for r in page]:
                raise ValidationError('A selecção mudou. Actualize a página.')
            valid = scale_form.is_valid()
            for row in page: valid = row['form'].is_valid() and valid
            if not valid: raise ValidationError('Verifique os valores assinalados.')
            with transaction.atomic():
                group = TrainingGroup.objects.select_for_update().select_related('assessment_scale').get(pk=group.pk)
                if snapshot(group) != token['snapshot']: raise ValidationError('Os dados mudaram. Actualize a página antes de gravar.')
                scale = getattr(group, 'assessment_scale', None)
                new_max = [scale_form.cleaned_data['initial_max'], scale_form.cleaned_data['final_max']]
                existing = DirectAssessment.objects.filter(enrollment__group=group)
                if scale and (existing.exclude(initial_points=None).exists() or existing.exclude(final_points=None).exists()) and new_max != [scale.initial_max, scale.final_max]:
                    raise ValidationError('A escala está fixada pelos testes existentes.')
                changes = []
                for row in page:
                    e = row['enrollment']; a = getattr(e, 'direct_assessment', None); data = row['form'].cleaned_data
                    for f, maximum in zip(FIELDS[:2], new_max):
                        if data[f] is not None and data[f] > maximum: raise ValidationError('Uma pontuação ultrapassa o máximo do teste.')
                    before = {f: getattr(a, f) if a else None for f in FIELDS}
                    if before == data: continue
                    if any(before[f] is not None and before[f] != data[f] for f in FIELDS) and not scale_form.cleaned_data['reason'].strip():
                        raise ValidationError('Indique o motivo para corrigir um valor já registado.')
                    changes.append((e, a, before, data))
                if not scale: scale = AssessmentScale(group=group)
                scale.initial_max, scale.final_max = new_max; scale.revision += 1; scale.full_clean(); scale.save()
                for e, a, before, data in changes:
                    a = a or DirectAssessment(enrollment=e)
                    for f in FIELDS: setattr(a, f, data[f])
                    a.updated_by = request.user.get_username(); a.full_clean(); a.save()
                AuditEvent.objects.create(actor=request.user.get_username(), action='DIRECT_ASSESSMENTS_SAVED', detail=json.dumps(dict(group=group.pk, course=course.pk,
                    maxima=new_max, reason=scale_form.cleaned_data['reason'], changes=[dict(enrollment=e.pk, before=b, after=d) for e, a, b, d in changes]), default=str, ensure_ascii=False))
            return redirect(request.get_full_path())
        except (ValidationError, signing.BadSignature, KeyError, TypeError) as exc:
            error = '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Sessão de edição inválida. Actualize a página.'
    token = request.POST.get('token', '') if request.method == 'POST' else (signing.dumps(dict(user=request.user.pk, ids=[r['enrollment'].pk for r in page], snapshot=snapshot(group)), salt='direct-assessment') if group else '')
    query = f'course={course.pk}' if course else ''
    if group: query += f'&group={group.pk}'
    response = render(request, 'direct_assessments.html', dict(course=course, group=group, courses=Course.objects.order_by('title'),
        groups=TrainingGroup.objects.filter(course=course) if course else [], summary=summary, page=page, editable=editable,
        scale_form=scale_form, token=token, error=error, query=query))
    response['Cache-Control'] = 'private, no-store'
    return response

from .professional_ui import person_ids
from .training_areas import AREAS
from . import data_validation as validation
from contextlib import closing
from .interface_chart import source_chart
from .ux_presentation import display_rows as presentation_rows
import io
import zipfile
import uuid
import sqlite3
import tempfile
from functools import wraps
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.forms.models import model_to_dict
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from django.views.decorators.http import require_POST, require_http_methods
from .forms import EmployeeForm, TrainingForm
from .models import AuditEvent, Employee, Training
from .analytics import statistics, distribution, profile_fields, format_phone, years_since, registration_header_fields

class LocalLoginView(LoginView):
    template_name = 'login.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.session.set_expiry(14 * 86400 if self.request.POST.get('remember') else 0)
        return response

def employee_fields(employee):
    return profile_fields(validation.display_employee(employee))

def staff_required(view):

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped

def record(request, action, detail):
    AuditEvent.objects.create(actor=request.user.username, action=action, detail=detail)

def employee_permission_required(action):
    """Legacy forms use the same model permissions as controlled corrections."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not (user.is_authenticated and user.is_active and user.is_staff
                    and user.has_perms(('training.view_employee', 'training.' + action))):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator

@login_required
def home(request):
    employees = validation.worker_queryset(Employee.objects.all()).annotate(training_count=Count('trainings', filter=~Q(trainings__interface_state='legacy_preserved'))).order_by('name', 'number')
    q = request.GET.get('q', '').strip()
    direction = request.GET.get('direction', '')
    status = request.GET.get('status', '')
    if q:
        employees = employees.filter(Q(pk__in=person_ids(q)))
    if direction:
        employees = employees.filter(direction=direction)
    if status == 'trained':
        employees = employees.filter(training_count__gt=0)
    elif status == 'untrained':
        employees = employees.filter(training_count=0)
    selected = request.GET.get('employee', '')
    employee = Employee.objects.filter(pk=int(selected)).first() if selected.isdecimal() and len(selected) < 18 else None
    total = Employee.objects.count()
    trained = Employee.objects.filter(trainings__interface_state__in=['operational', 'consolidated']).values('pk').distinct().count()
    return render(request, 'home.html', {'page': Paginator(employees, 30).get_page(request.GET.get('page')), 'q': q, 'direction': direction, 'status': status, 'directions': Employee.objects.exclude(direction='').order_by('direction').values_list('direction', flat=True).distinct(), 'employee_count': total, 'trained_count': trained, 'untrained_count': total - trained, 'coverage': trained / total * 100 if total else 0, 'employee': validation.display_employee(employee) if employee is not None else None, 'profile_fields': employee_fields(employee) if employee else [], 'selected_stats': statistics(employee.trainings.all()) if employee else {}, 'stats': SimpleLazyObject(lambda: statistics(Training.objects.all()))})

@login_required
@employee_permission_required('add_employee')
@require_http_methods(['GET', 'POST'])
def employee_create(request):
    form = EmployeeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            employee = form.save()
            record(request, 'ADD_EMPLOYEE', str(employee))
        return redirect('employee_detail', pk=employee.pk)
    return render(request, 'form.html', {'form': form, 'title': 'Novo funcionário'})

@login_required
@employee_permission_required('change_employee')
@require_http_methods(['GET', 'POST'])
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    validation.require_profile_access(request.user,employee)
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            employee = form.save()
            record(request, 'EDIT_EMPLOYEE', str(employee))
        return redirect('employee_data', pk=employee.pk)
    return render(request, 'form.html', {'form': form, 'title': 'Editar dados do formando', 'employee': employee})

@login_required
def employee_data(request, pk=None):
    if pk is not None and Employee.all_objects.filter(pk=pk, registry_active=False).exists():
        from .interface_archive import employee
        return employee(request, pk)
    if pk is None:
        scope = request.GET.get('scope', 'official')
        if scope not in ('official', 'pending', 'pending_historical', 'pending_conflict'):
            from django.http import Http404
            raise Http404
        can_review = validation.enabled() and validation.can_review(request.user, 'employee')
        if scope != 'official' and not can_review:
            raise PermissionDenied
        employees = validation.worker_queryset(Employee.objects.order_by('name', 'number'), scope)
        q = request.GET.get('q', '').strip()
        if q:
            employees = employees.filter(Q(pk__in=person_ids(q)))
        page = Paginator(employees, 30).get_page(request.GET.get('page'))
        page.object_list = [validation.display_employee(person) for person in page.object_list]
        if scope != 'official':
            p = validation.policy()
            for person in page:
                person.validation_identity = p.decision('employee', person.pk, 'identity')['label']
                person.validation_roster = p.decision('employee', person.pk, 'roster')['label']
        return render(request, 'employee_selector_legacy.html', {
            'page': page, 'q': q, 'selector_scope': scope,
            'selector_pending': scope != 'official', 'selector_can_review': can_review,
            'selector_validation': validation.enabled()})
    employee = get_object_or_404(Employee, pk=pk)
    validation.require_profile_access(request.user,employee)
    from .ux_presentation import display_rows, certificate_cards, competencies as clean_competencies
    rows = display_rows(validation.history_queryset(employee.trainings.all()))
    fields = registration_header_fields(validation.display_employee(employee))
    screen_fields = [fields[i] for i in [0, 6, 2, 8, 3, 5, 1]]
    screen_fields += [('Tel.', ' | '.join((fields[i][1] or '—' for i in [7, 9]))), fields[10]]
    competencies = clean_competencies(rows)
    groups = distribution(rows)
    from .documents import DocumentForm
    document_form = DocumentForm(employee=employee, prefix=None) if request.user.is_staff else None
    from .document_views import profile_context
    document_context=profile_context(request.user,employee,rows)
    return render(request, 'employee_data.html', {'employee': validation.display_employee(employee) if employee is not None else None, 'data_fields': screen_fields, 'trainings': rows[:5], 'print_fields': fields, 'print_name': ' '.join(employee.name.split()[::max(1, len(employee.name.split()) - 1)]), 'today': timezone.localdate(), 'stats': statistics(rows), 'distribution': groups, 'history_total': len(rows), 'competencies': competencies, 'ux_certificates':certificate_cards(rows,request.user), 'ux_certificates':certificate_cards(rows,request.user), 'has_hours': any(r.completed_hours is not None and r.completed_hours>0 for r in rows), 'unconfirmed_certificates': sum((row.certified is None for row in rows)), 'document_form': document_form, 'private_documents': employee.documents.filter(active=True).select_related('training') if request.user.is_staff else [],**document_context})

@login_required
def employee_detail(request, pk):
    if pk is not None and Employee.all_objects.filter(pk=pk, registry_active=False).exists():
        from .interface_archive import employee
        return employee(request, pk)
    employee = get_object_or_404(Employee, pk=pk)
    validation.require_profile_access(request.user,employee)
    all_rows = validation.history_queryset(employee.trainings.all())
    rows = all_rows
    year, area = (request.GET.get('year', ''), request.GET.get('area', ''))
    if year.isdecimal() and len(year) == 4:
        rows = rows.filter(start__year=int(year))
    if area:
        rows = rows.filter(area=area)
    graded = [t for t in rows.order_by('start', 'end', 'pk') if t.presentation_grade is not None]
    dots = [{'x': round(25 + i * 260 / (len(graded) - 1)) if len(graded) > 1 else 155, 'y': round(130 - float(t.presentation_grade) * 6), 'title': t.title, 'grade': t.presentation_grade, 'date_label': f'{t.start:%d/%m/%Y} — {t.end:%d/%m/%Y}', 'trainer': t.trainer, 'simulated': t.simulated_grade is not None} for i, t in enumerate(graded)]
    return render(request, 'employee.html', {'employee': validation.display_employee(employee) if employee is not None else None, 'trainings': presentation_rows(rows), 'stats': statistics(rows), 'profile_fields': employee_fields(employee), 'latest_location': all_rows.first().location if all_rows.exists() else '', 'years': sorted({t.start.year for t in all_rows}, reverse=True), 'areas': list(AREAS), 'year': year, 'area': area, 'chart_area': '25,130 ' + ' '.join((f"{p['x']},{p['y']}" for p in dots)) + ' 285,130' if len(dots) > 1 else '', 'chart_first_date': graded[0].start.strftime('%d/%m/%Y') if graded else '', 'chart_last_date': graded[-1].start.strftime('%d/%m/%Y') if graded else '', 'chart_points': ' '.join((f"{p['x']},{p['y']}" for p in dots)), 'chart_dots': dots, **source_chart(rows, request.GET.get('scale', ''))})

@login_required
@staff_required
def training_create(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    validation.require_profile_access(request.user,employee)
    form = TrainingForm(request.POST or None, instance=Training(employee=employee))
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            training = form.save()
            record(request, 'ADD_TRAINING', f'{employee.number} / #{training.pk} / {training.title}')
        messages.success(request, 'Formação guardada na base de dados local.')
        return redirect('employee_detail', pk=pk)
    return render(request, 'form.html', {'form': form, 'title': 'Registar formação', 'employee': employee})

@login_required
@staff_required
def training_edit(request, pk):
    training = get_object_or_404(Training, pk=pk)
    before = str(model_to_dict(training))
    form = TrainingForm(request.POST or None, instance=training, initial={'revision': training.version})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            values = {field: getattr(form.instance, field) for field in form.Meta.fields}
            changed = Training.objects.filter(pk=pk, version=form.cleaned_data['revision']).update(**values, version=training.version + 1)
            if changed:
                record(request, 'UPDATE_TRAINING', f'#{pk} BEFORE {before}\nAFTER {model_to_dict(form.instance)}')
                messages.success(request, 'Formação actualizada.')
                return redirect('employee_detail', pk=training.employee_id)
        form.add_error(None, 'Este registo foi alterado noutra janela. Reabra a página antes de editar.')
    return render(request, 'form.html', {'form': form, 'title': 'Corrigir formação', 'employee': training.employee})

@login_required
@staff_required
def training_delete(request, pk):
    from .models import HistoricalInterfaceLink
    protected = HistoricalInterfaceLink.objects.filter(training_id=pk).select_related('participation', 'training').first()
    if protected and (not protected.participation.source_key.startswith('manual:')):
        messages.error(request, 'Este histórico está comprovado nas fontes. Utilize a correção para conservar o registo e a auditoria.')
        return redirect('employee_detail', pk=protected.training.employee_id)
    training = get_object_or_404(Training, pk=pk)
    if training.documents.exists() or training.exams.exists() or training.evaluation_cycles.exists() or hasattr(training, 'evaluation') or hasattr(training, 'reaction'):
        messages.error(request, 'Esta participação tem documentos privados ou avaliações. Conserve o histórico e corrija os dados, se necessário.')
        return redirect('employee_detail', pk=training.employee_id)
    if training.group_id:
        messages.error(request, 'Este registo pertence a um grupo. Corrija a participação ou registe a desistência; o histórico da inscrição será conservado.')
        return redirect('group_detail', pk=training.group_id)
    if request.method == 'POST':
        with transaction.atomic():
            record(request, 'DELETE_TRAINING', str(model_to_dict(training)))
            if protected:
                protected.participation.active = False
                protected.participation.save(update_fields=['active'])
                if not protected.participation.edition.participations.filter(active=True).exists():
                    protected.participation.edition.active = False
                    protected.participation.edition.save(update_fields=['active'])
                protected.delete()
            employee_id = training.employee_id
            training.delete()
        messages.success(request, 'Formação eliminada. O funcionário foi preservado.')
        return redirect('employee_detail', pk=employee_id)
    return render(request, 'delete.html', {'training': training})

@login_required
def employee_print(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    validation.require_profile_access(request.user,employee)
    from .ux_presentation import display_rows, certificate_cards, competencies as clean_competencies
    rows = display_rows(validation.history_queryset(employee.trainings.all()))
    stats = statistics(rows)
    source = employee.source_data or {}
    age, seniority = (years_since(employee.birth_date), years_since(employee.admission_date))
    return render(request, 'print.html', {'employee': validation.display_employee(employee) if employee is not None else None, 'trainings': rows, 'stats': stats, 'today': timezone.localdate(), 'distribution': distribution(rows), 'print_name': ' '.join(employee.name.split()[::max(1, len(employee.name.split()) - 1)]), 'print_fields': registration_header_fields(validation.display_employee(employee))})

@login_required
@staff_required
def audit(request):
    page=Paginator(AuditEvent.objects.all(),30).get_page(request.GET.get('page'))
    from .audit_presentation import present
    for event in page.object_list:present(event)
    return render(request,'audit.html',{'page':page})

@login_required
@staff_required
@require_POST
def backup(request):
    if not request.user.is_superuser:
        raise PermissionDenied('A cópia integral contém respostas privadas e exige um superutilizador.')
    record(request, 'BACKUP', 'Exportação da base, relatórios e documentos privados')
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / 'backup.sqlite3'
        with closing(sqlite3.connect(settings.DATABASES['default']['NAME'])) as source, closing(sqlite3.connect(target)) as destination, source, destination:
            source.backup(destination)
        with closing(sqlite3.connect(target)) as copy, copy:
            has_archive = copy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_executivereport'").fetchone()
            report_ids = [str(uuid.UUID(x[0])) for x in copy.execute('SELECT id FROM training_executivereport')] if has_archive else []
            has_documents = copy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_privatedocument'").fetchone()
            documents = list(copy.execute('SELECT id, sha256, size FROM training_privatedocument')) if has_documents else []
        if report_ids or documents:
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(target, 'pmf.sqlite3')
                for identifier in report_ids:
                    pdf = Path(settings.DATA_DIR) / 'reports' / f'{identifier}.pdf'
                    if not pdf.is_file():
                        from django.http import HttpResponse
                        return HttpResponse('Falta um PDF do arquivo. Reponha o ficheiro antes de exportar a cópia completa.', status=409)
                    bundle.write(pdf, f'reports/{identifier}.pdf')
                import hashlib
                for raw_id, expected_hash, expected_size in documents:
                    identifier = str(uuid.UUID(raw_id))
                    pdf = Path(settings.DATA_DIR) / 'documents' / f'{identifier}.pdf'
                    if pdf.is_symlink() or not pdf.is_file():
                        from django.http import HttpResponse
                        return HttpResponse('Falta um documento privado. Reponha o ficheiro antes de exportar.', status=409)
                    content = pdf.read_bytes()
                    if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
                        from django.http import HttpResponse
                        return HttpResponse('Um documento privado falhou a verificação de integridade.', status=409)
                    bundle.writestr(f'documents/{identifier}.pdf', content)
                secret = Path(settings.DATA_DIR) / 'secret.key'
                if secret.is_file():
                    bundle.write(secret, 'secret.key')
                bundle.writestr('REPOR_COPIA.txt', 'Com a plataforma parada, utilize: python manage.py restore_backup COPIA.zip --destination NOVA_PASTA. A pasta de destino não pode existir. A recuperação valida a base, relatórios e documentos privados antes de criar o destino. Depois configure PMF_DATA_DIR para a pasta recuperada e arranque a aplicação. Guarde a instalação anterior. Nunca extraia sobre uma base em utilização.')
            payload.seek(0)
            return FileResponse(payload, as_attachment=True, filename=f'PMF-{timezone.now():%Y%m%d-%H%M%S}.zip')
        payload = io.BytesIO(target.read_bytes())
    return FileResponse(payload, as_attachment=True, filename=f'PMF-{timezone.now():%Y%m%d-%H%M%S}.sqlite3')

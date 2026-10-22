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
from django.views.decorators.http import require_POST
from .forms import EmployeeForm, TrainingForm
from .models import AuditEvent, Employee, Training
from .analytics import statistics, distribution, profile_fields, format_phone, years_since


class LocalLoginView(LoginView):
    template_name = 'login.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.session.set_expiry(14 * 86400 if self.request.POST.get('remember') else 0)
        return response


def employee_fields(employee):
    return profile_fields(employee)


def staff_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def record(request, action, detail):
    AuditEvent.objects.create(actor=request.user.username, action=action, detail=detail)



@login_required
def home(request):
    employees = Employee.objects.annotate(training_count=Count('trainings')).order_by('name', 'number')
    q = request.GET.get('q', '').strip()
    direction = request.GET.get('direction', '')
    status = request.GET.get('status', '')
    if q:
        employees = employees.filter(Q(name__icontains=q) | Q(number__icontains=q))
    if direction:
        employees = employees.filter(direction=direction)
    if status == 'trained':
        employees = employees.filter(training_count__gt=0)
    elif status == 'untrained':
        employees = employees.filter(training_count=0)
    selected = request.GET.get('employee', '')
    employee = Employee.objects.filter(pk=int(selected)).first() if selected.isdecimal() and len(selected) < 18 else None
    total = Employee.objects.count()
    trained = Employee.objects.filter(trainings__isnull=False).distinct().count()
    return render(request, 'home.html', {
        'page': Paginator(employees, 30).get_page(request.GET.get('page')),
        'q': q, 'direction': direction, 'status': status,
        'directions': Employee.objects.exclude(direction='').order_by('direction').values_list('direction', flat=True).distinct(),
        'employee_count': total, 'trained_count': trained,
        'untrained_count': total - trained, 'coverage': trained / total * 100 if total else 0,
        'employee': employee,
        'profile_fields': employee_fields(employee) if employee else [],
        'selected_stats': statistics(employee.trainings.all()) if employee else {},
        'stats': statistics(Training.objects.all()),
    })


@login_required
@staff_required
def employee_create(request):
    form = EmployeeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            employee = form.save()
            record(request, 'ADD_EMPLOYEE', str(employee))
        return redirect('employee_detail', pk=employee.pk)
    return render(request, 'form.html', {'form': form, 'title': 'Novo funcionário'})


@login_required
@staff_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            employee = form.save()
            record(request, 'EDIT_EMPLOYEE', str(employee))
        return redirect('employee_data', pk=employee.pk)
    return render(request, 'form.html', {'form': form, 'title': 'Editar dados do formando', 'employee': employee})


@login_required
def employee_data(request, pk=None):
    if pk is None:
        employees = Employee.objects.order_by('name', 'number')
        q = request.GET.get('q', '').strip()
        if q:
            employees = employees.filter(Q(name__icontains=q) | Q(number__icontains=q))
        return render(request, 'employee_data.html', {
            'page': Paginator(employees, 30).get_page(request.GET.get('page')), 'q': q})
    employee = get_object_or_404(Employee, pk=pk)
    rows = list(employee.trainings.all())
    fields = profile_fields(employee)
    competencies, seen = [], set()
    for row in rows:
        if row.title not in seen and len(competencies) < 6:
            seen.add(row.title)
            competencies.append({'training': row,
                'percent': max(0, min(100, float(row.grade) * 5)) if row.grade is not None else None})
    groups = distribution(rows)
    return render(request, 'employee_data.html', {'employee': employee, 'data_fields': fields,
        'trainings': rows[:5], 'stats': statistics(rows), 'distribution': groups, 'history_total': len(rows),
        'competencies': competencies, 'has_hours': bool(groups),
        'unconfirmed_certificates': sum(row.certified is None for row in rows)})



@login_required
def employee_detail(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    all_rows = employee.trainings.all()
    rows = all_rows
    year, area = request.GET.get('year', ''), request.GET.get('area', '')
    if year.isdecimal() and len(year) == 4:
        rows = rows.filter(start__year=int(year))
    if area:
        rows = rows.filter(area=area)
    graded = [t for t in reversed(list(rows)) if t.grade is not None]
    dots = [{'x': round(25 + i * 260 / max(len(graded) - 1, 1)),
             'y': round(130 - float(t.grade) * 6), 'title': t.title, 'grade': t.grade}
            for i, t in enumerate(graded)]
    return render(request, 'employee.html', {'employee': employee,
        'trainings': rows, 'stats': statistics(rows), 'profile_fields': employee_fields(employee),
        'latest_location': all_rows.first().location if all_rows.exists() else '',
        'years': sorted({t.start.year for t in all_rows}, reverse=True),
        'areas': sorted({t.area for t in all_rows}), 'year': year, 'area': area,
        'chart_points': ' '.join(f"{p['x']},{p['y']}" for p in dots), 'chart_dots': dots})


@login_required
@staff_required
def training_create(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
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
            # Compare-and-swap prevents an older browser tab from overwriting a newer edit.
            values = {field: getattr(form.instance, field) for field in form.Meta.fields}
            changed = Training.objects.filter(pk=pk, version=form.cleaned_data['revision']).update(
                **values, version=training.version + 1)
            if changed:
                record(request, 'UPDATE_TRAINING', f'#{pk} BEFORE {before}\nAFTER {model_to_dict(form.instance)}')
                messages.success(request, 'Formação actualizada.')
                return redirect('employee_detail', pk=training.employee_id)
        form.add_error(None, 'Este registo foi alterado noutra janela. Reabra a página antes de editar.')
    return render(request, 'form.html', {'form': form, 'title': 'Corrigir formação', 'employee': training.employee})


@login_required
@staff_required
def training_delete(request, pk):
    training = get_object_or_404(Training, pk=pk)
    if (training.documents.exists() or training.exams.exists() or
            hasattr(training, "evaluation") or hasattr(training, "reaction")):
        messages.error(request, 'Esta participação tem documentos privados ou avaliações. Conserve o histórico e corrija os dados, se necessário.')
        return redirect('employee_detail', pk=training.employee_id)
    if training.group_id:
        messages.error(request, 'Este registo pertence a um grupo. Corrija a participação ou registe a desistência; o histórico da inscrição será conservado.')
        return redirect('group_detail', pk=training.group_id)
    if request.method == 'POST':
        with transaction.atomic():
            record(request, 'DELETE_TRAINING', str(model_to_dict(training)))
            employee_id = training.employee_id
            training.delete()
        messages.success(request, 'Formação eliminada. O funcionário foi preservado.')
        return redirect('employee_detail', pk=employee_id)
    return render(request, 'delete.html', {'training': training})


@login_required
def employee_print(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    rows = list(employee.trainings.all())
    stats = statistics(rows)
    source = employee.source_data or {}
    age, seniority = years_since(employee.birth_date), years_since(employee.admission_date)
    return render(request, 'print.html', {'employee': employee, 'trainings': rows,
        'stats': stats, 'today': timezone.localdate(), 'distribution': distribution(rows),
        'print_name': ' '.join(employee.name.split()[::max(1, len(employee.name.split()) - 1)]),
        'print_fields': profile_fields(employee)})


@login_required
@staff_required
def audit(request):
    return render(request, 'audit.html', {'page': Paginator(AuditEvent.objects.all(), 30).get_page(request.GET.get('page'))})


@login_required
@staff_required
@require_POST
def backup(request):
    record(request, 'BACKUP', 'Exportação da base, relatórios e documentos privados')
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / 'backup.sqlite3'
        with sqlite3.connect(settings.DATABASES['default']['NAME']) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        with sqlite3.connect(target) as copy:
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

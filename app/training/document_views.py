import hashlib
import io
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from . import documents as service
from .models import Employee, Training, PrivateDocument, TrainingDocumentState


def page(request, employee, form, title):
    from django import forms
    for field in form.fields.values():
        if not isinstance(field.widget, (forms.HiddenInput, forms.CheckboxInput)):
            field.widget.attrs['class'] = 'gf-field'
    return render(request, 'documents/form.html', {'employee':employee,'form':form,'title':title})


@login_required
@require_http_methods(['GET','POST'])
def upload(request, pk):
    service.require(request.user, 'add_privatedocument')
    employee = get_object_or_404(Employee, pk=pk)
    form = service.UploadForm(request.POST if request.method == 'POST' else None, request.FILES or None, employee=employee)
    if request.method == 'POST' and form.is_valid():
        try:
            service.store_document(request.user, employee, form)
            return redirect('employee_data', pk=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return page(request, employee, form, 'Arquivar PDF privado')


@login_required
@require_http_methods(['GET'])
def download(request, document_id):
    service.require(request.user, 'view_privatedocument')
    document = get_object_or_404(PrivateDocument, pk=document_id)
    content = bytes(document.content)
    if hashlib.sha256(content).hexdigest() != document.sha256 or len(content) != document.size:
        return HttpResponse('O documento não passou a verificação de integridade.', status=409)
    response = FileResponse(io.BytesIO(content), content_type='application/pdf',
        as_attachment=request.GET.get('view') != '1', filename=f'{document.kind}-{document.id}.pdf')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['X-Frame-Options'] = 'DENY'
    return response


@login_required
@require_http_methods(['GET','POST'])
def delivery(request, pk):
    service.require(request.user, 'change_trainingdocumentstate')
    training = get_object_or_404(Training, pk=pk)
    state = TrainingDocumentState.objects.filter(training=training).first()
    initial = {'revision':state.revision if state else 0}
    if not state:
        initial.update(learner_delivered=training.certified, learner_date=training.certificate_delivery_date)
    form = service.DeliveryForm(request.POST if request.method == 'POST' else None,
        instance=state or TrainingDocumentState(training=training), initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            service.save_delivery(request.user, training, request.POST)
            return redirect('employee_data', pk=training.employee_id)
        except (ValidationError, OperationalError) as exc:
            form.add_error(None, 'Registo não guardado: ' + (str(exc) if isinstance(exc, ValidationError) else 'operação concorrente; reabra a página.'))
    return page(request, training.employee, form, 'Emissão e entregas — ' + training.title)


@login_required
@require_http_methods(['GET','POST'])
def professional(request, pk):
    service.require(request.user, 'change_employee')
    employee = get_object_or_404(Employee, pk=pk)
    form = service.ProfessionalForm(request.POST if request.method == 'POST' else None, initial={
        'category':employee.category,'function':employee.function,'revision':service.professional_revision(employee)})
    if request.method == 'POST' and form.is_valid():
        try:
            service.correct_professional(request.user, pk, request.POST)
            return redirect('employee_data', pk=pk)
        except (ValidationError, OperationalError) as exc:
            form.add_error(None, 'Correcção não guardada: ' + (str(exc) if isinstance(exc, ValidationError) else 'operação concorrente; reabra a página.'))
    return page(request, employee, form, 'Corrigir categoria e função')


def profile_context(user, employee, rows):
    if not getattr(settings, 'PMF_DOCUMENTS_ENABLED', False):
        return {}
    from .source_achievement import attach
    from .analytics import documentary_states
    attach(rows);documentary_states(rows)
    return {'document_archive_enabled':True,
        'can_view_documents':service.authorised(user,'view_privatedocument'),
        'can_upload_documents':service.authorised(user,'add_privatedocument'),
        'can_manage_deliveries':service.authorised(user,'change_trainingdocumentstate'),
        'can_correct_professional':service.authorised(user,'change_employee'),
        'private_documents':employee.private_documents.defer('content').all() if service.authorised(user,'view_privatedocument') else [],
        'document_rows':[service.delivery_summary(t) for t in rows]}

"""Private, immutable PDFs and separately recorded certificate states."""
import hashlib
import io
import os
from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .forms import StyledModelForm
from .models import Employee, Training, PrivateDocument
from .views import staff_required, record

MAX_PDF_SIZE = 10 * 1024 * 1024


def document_path(identifier):
    return Path(settings.DATA_DIR) / 'documents' / f'{identifier}.pdf'


def checked_payload(document):
    path = document_path(document.pk)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Documento indisponível. Reponha a cópia de segurança.')
    payload = path.read_bytes()
    if len(payload) != document.size or hashlib.sha256(payload).hexdigest() != document.sha256:
        raise ValueError('A integridade do documento não corresponde ao registo.')
    return payload


class DocumentForm(StyledModelForm):
    file = forms.FileField(label='PDF (máximo 10 MB)', widget=forms.FileInput(attrs={'accept': '.pdf,application/pdf'}))

    class Meta:
        model = PrivateDocument
        fields = ['kind', 'training', 'note']

    def __init__(self, *args, employee, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.employee = employee
        self.fields['training'].queryset = Training.objects.filter(employee=employee)
        self.fields['training'].label_from_instance = lambda row: f'{row.title} — {row.start:%d/%m/%Y} (#{row.pk})'
        self.fields['training'].help_text = 'Obrigatória para certificados/comprovativos; deixe vazia para CV.'

    def clean_file(self):
        upload = self.cleaned_data['file']
        if not upload.name.lower().endswith('.pdf') or upload.size > MAX_PDF_SIZE:
            raise ValidationError('Seleccione um PDF até 10 MB.')
        payload = upload.read(MAX_PDF_SIZE + 1)
        if len(payload) > MAX_PDF_SIZE or not payload.startswith(b'%PDF-') or b'%%EOF' not in payload[-1024:]:
            raise ValidationError('O ficheiro não apresenta uma estrutura básica de PDF válida.')
        self.payload = payload
        upload.seek(0)
        return upload


def tri_state(label):
    return forms.TypedChoiceField(label=label, required=False,
        choices=[('unknown', 'Por confirmar'), ('true', 'Sim'), ('false', 'Não')],
        coerce=lambda value: {'unknown': None, 'true': True, 'false': False}[value], empty_value=None)


class CertificateForm(StyledModelForm):
    certificate_issued = tri_state('Certificado emitido')
    certified = tri_state('Entrega confirmada ao participante')
    certificate_hr_delivered = tri_state('Entrega confirmada aos RH')
    revision = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Training
        fields = ['certificate_issued', 'certificate_issue_date', 'certificate_reference',
            'certificate_declaration', 'certificate_declared_by', 'certificate_declaration_source',
            'certificate_declaration_date', 'certified', 'certificate_delivery_date',
            'certificate_hr_delivered', 'certificate_hr_delivery_date']
        widgets = {name: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}) for name in
            ['certificate_issue_date', 'certificate_declaration_date', 'certificate_delivery_date', 'certificate_hr_delivery_date']}
        widgets.update({name: forms.Textarea(attrs={'rows': 3}) for name in
            ['certificate_declaration', 'certificate_declaration_source']})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['revision'] = self.instance.version
        for name in ['certificate_issued', 'certified', 'certificate_hr_delivered']:
            value = getattr(self.instance, name)
            self.initial[name] = 'true' if value is True else 'false' if value is False else 'unknown'
        self.fields['certificate_declaration_date'].help_text = 'Data da declaração; não substitui a data efectiva de entrega.'
        self.fields['certificate_issue_date'].help_text = 'Deixe vazia quando não existir data comprovada.'

    @property
    def design_groups(self):
        return [(title, [self[name] for name in names]) for title, names in [
            ('Emissão', ['certificate_issued', 'certificate_issue_date', 'certificate_reference']),
            ('Declaração e origem', ['certificate_declaration', 'certificate_declared_by',
                                   'certificate_declaration_source', 'certificate_declaration_date']),
            ('Entrega ao participante', ['certified', 'certificate_delivery_date']),
            ('Entrega aos RH', ['certificate_hr_delivered', 'certificate_hr_delivery_date'])]]

    def clean(self):
        data = super().clean()
        if data.get('certificate_declaration'):
            for field in ['certificate_declared_by', 'certificate_declaration_source']:
                if not data.get(field):
                    self.add_error(field, 'Indique o responsável e a origem da declaração, sem inventar dados.')
        return data


@login_required
@staff_required
def index(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    form = DocumentForm(request.POST if request.method == 'POST' else None,
                        request.FILES if request.method == 'POST' else None, employee=employee)
    if request.method == 'POST' and form.is_valid():
        document = form.save(commit=False)
        document.actor = request.user.username
        document.original_name = Path(form.cleaned_data['file'].name.replace('\\', '/')).name[:200]
        document.sha256 = hashlib.sha256(form.payload).hexdigest()
        document.size = len(form.payload)
        path = document_path(document.pk)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with transaction.atomic():
                document.full_clean()
                with path.open('xb') as output:
                    created = True
                    os.chmod(path, 0o600)
                    output.write(form.payload)
                document.save()
                record(request, 'ADD_PRIVATE_DOCUMENT', f'{document.pk}: {document.kind}, trabalhador {employee.pk}')
        except Exception:
            if created:
                path.unlink(missing_ok=True)
            raise
        messages.success(request, 'Documento privado guardado. O envio não confirma emissão ou entrega.')
        return redirect('employee_documents', pk=pk)
    return render(request, 'documents.html', {'employee': employee, 'form': form,
        'documents': employee.documents.select_related('training').all()})


@login_required
@staff_required
def download(request, pk):
    document = get_object_or_404(PrivateDocument, pk=pk)
    try:
        payload = checked_payload(document)
    except ValueError as exc:
        return HttpResponse(str(exc), status=409)
    response = FileResponse(io.BytesIO(payload), content_type='application/pdf', as_attachment=True,
                            filename=document.original_name)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@staff_required
@require_POST
def archive(request, pk):
    document = get_object_or_404(PrivateDocument, pk=pk)
    reason = request.POST.get('reason', '').strip()
    action = request.POST.get('action')
    if not reason or len(reason) > 1000 or action not in ('archive', 'restore'):
        return HttpResponse('Indique uma acção válida e um motivo até 1.000 caracteres.', status=400)
    with transaction.atomic():
        PrivateDocument.objects.filter(pk=pk).update(active=action == 'restore')
        record(request, 'PRIVATE_DOCUMENT_STATE', f'{pk}: {action}; {reason}')
    return redirect('employee_documents', pk=document.employee_id)


@login_required
@staff_required
def certificate_edit(request, pk):
    training = get_object_or_404(Training, pk=pk)
    before = {field: str(getattr(training, field)) for field in CertificateForm.Meta.fields}
    form = CertificateForm(request.POST if request.method == 'POST' else None, instance=training)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            values = {field: getattr(form.instance, field) for field in form.Meta.fields}
            changed = Training.objects.filter(pk=pk, version=form.cleaned_data['revision']).update(
                **values, version=form.cleaned_data['revision'] + 1)
            if changed:
                record(request, 'UPDATE_CERTIFICATE', f'Participação {pk}; BEFORE {before}; AFTER {values}')
                messages.success(request, 'Estados do certificado actualizados; declaração e entregas mantidas separadas.')
                return redirect('employee_detail', pk=training.employee_id)
        form.add_error(None, 'A participação mudou noutra janela. Reabra o certificado antes de guardar.')
    return render(request, 'certificate_form.html', {'form': form, 'employee': training.employee,
        'training': training, 'title': 'Certificado e entregas'})

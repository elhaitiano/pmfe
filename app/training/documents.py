from .professional_ui import person_ids
from .models import AuditEvent, Employee, PrivateDocument, Training, TrainingDocumentState
from django.forms.models import model_to_dict
from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django import forms
from pypdf import PdfReader
from pathlib import Path
import json
import io
import hashlib
'Private, immutable PDFs and separately recorded certificate states.'
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
from django.urls import reverse
from .forms import StyledModelForm
from .models import Employee, Training, PrivateDocument
from .views import staff_required, record
MAX_PDF_SIZE = 10 * 1024 * 1024

def document_path(identifier):
    return Path(settings.DATA_DIR) / 'documents' / f'{identifier}.pdf'

def checked_payload(document):
    path = document_path(document.pk)
    if path.is_symlink():
        raise ValueError('Documento indisponível. Reponha a cópia de segurança.')
    if path.is_file():
        payload=path.read_bytes()
    elif document.content:
        payload=bytes(document.content)
    else:
        raise ValueError('Documento indisponível. Reponha a cópia de segurança.')
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
        self.fields['kind'].choices = [(k, v) for k, v in self.fields['kind'].choices if k != 'hr_receipt']
        self.fields['training'].queryset = Training.objects.filter(employee=employee)
        self.fields['training'].label_from_instance = lambda row: f'{row.title} — {row.start:%d/%m/%Y} (#{row.pk})'
        self.fields['training'].help_text = 'Obrigatória para certificados/comprovativos; deixe vazia para CV ou Bilhete de Identidade.'

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
    return forms.TypedChoiceField(label=label, required=False, choices=[('unknown', 'Nao registado'), ('true', 'Sim'), ('false', 'Não')], coerce=lambda value: {'unknown': None, 'true': True, 'false': False}[value], empty_value=None)

class CertificateForm(StyledModelForm):
    certificate_issued = tri_state('Certificado emitido')
    certified = tri_state('Entrega confirmada ao participante')
    revision = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Training
        fields = ['certificate_issued', 'certificate_issue_date', 'certificate_reference', 'certificate_declaration', 'certificate_declared_by', 'certificate_declaration_source', 'certificate_declaration_date', 'certified', 'certificate_delivery_date']
        widgets = {name: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}) for name in ['certificate_issue_date', 'certificate_declaration_date', 'certificate_delivery_date', 'certificate_hr_delivery_date']}
        widgets.update({name: forms.Textarea(attrs={'rows': 3}) for name in ['certificate_declaration', 'certificate_declaration_source']})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['revision'] = self.instance.version
        for name in ['certificate_issued', 'certified']:
            value = getattr(self.instance, name)
            self.initial[name] = 'true' if value is True else 'false' if value is False else 'unknown'
        self.fields['certificate_declaration_date'].help_text = 'Data da declaração; não substitui a data efectiva de entrega.'
        self.fields['certificate_issue_date'].help_text = 'Deixe vazia quando não existir data comprovada.'

    @property
    def design_groups(self):
        return [(title, [self[name] for name in names]) for title, names in [('Emissão', ['certificate_issued', 'certificate_issue_date', 'certificate_reference']), ('Declaração e origem', ['certificate_declaration', 'certificate_declared_by', 'certificate_declaration_source', 'certificate_declaration_date']), ('Entrega ao trabalhador', ['certified', 'certificate_delivery_date'])]]

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
    require(request.user,'add_privatedocument' if request.method=='POST' else 'view_privatedocument')
    employee = get_object_or_404(Employee, pk=pk)
    form = DocumentForm(request.POST if request.method == 'POST' else None, request.FILES if request.method == 'POST' else None, employee=employee)
    if request.method == 'GET' and request.GET.get('training'):
        selected = form.fields['training'].queryset.filter(pk=request.GET['training']).first() if request.GET['training'].isdigit() else None
        if selected:
            form.initial.update(training=selected.pk, kind='certificate')
    if request.method == 'POST' and form.is_valid():
        document = form.save(commit=False)
        document.actor = request.user.username
        document.original_name = Path(form.cleaned_data['file'].name.replace('\\', '/')).name[:200]
        document.reference = document.original_name
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
                    os.chmod(path, 384)
                    output.write(form.payload)
                document.save()
                record(request, 'ADD_PRIVATE_DOCUMENT', f'{document.pk}: {document.kind}, trabalhador {employee.pk}')
        except Exception:
            if created:
                path.unlink(missing_ok=True)
            raise
        messages.success(request, 'Documento privado guardado. O envio não confirma emissão ou entrega.')
        if request.POST.get('return_to') == 'files':
            return redirect(reverse('employee_data', args=[pk]) + '#arquivos')
        return redirect('employee_documents', pk=pk)
    return render(request, 'documents.html', {'employee': employee, 'form': form, 'return_to': 'files' if request.POST.get('return_to') == 'files' else '', 'documents': employee.documents.select_related('training').all()})

@login_required
@staff_required
def download(request, pk):
    require(request.user,'view_privatedocument')
    document = get_object_or_404(PrivateDocument, pk=pk)
    try:
        payload = checked_payload(document)
    except ValueError as exc:
        return HttpResponse(str(exc), status=409)
    response = FileResponse(io.BytesIO(payload), content_type='application/pdf', as_attachment=request.GET.get('view')!='1', filename=document.original_name)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['X-Frame-Options'] = 'DENY'
    return response

@login_required
@staff_required
@require_POST
def archive(request, pk):
    require(request.user,'change_privatedocument')
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
    require(request.user,'change_training')
    training = get_object_or_404(Training, pk=pk)
    before = {field: str(getattr(training, field)) for field in CertificateForm.Meta.fields}
    form = CertificateForm(request.POST if request.method == 'POST' else None, instance=training)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            values = {field: getattr(form.instance, field) for field in form.Meta.fields}
            changed = Training.objects.filter(pk=pk, version=form.cleaned_data['revision']).update(**values, version=form.cleaned_data['revision'] + 1)
            if changed:
                record(request, 'UPDATE_CERTIFICATE', f'Participação {pk}; BEFORE {before}; AFTER {values}')
                messages.success(request, 'Estados do certificado actualizados; declaração e entregas mantidas separadas.')
                return redirect('employee_detail', pk=training.employee_id)
        form.add_error(None, 'A participação mudou noutra janela. Reabra o certificado antes de guardar.')
    return render(request, 'certificate_form.html', {'form': form, 'employee': training.employee, 'training': training, 'title': 'Certificado e entregas'})

@login_required
@staff_required
def certificate_archive(request):
    require(request.user,'view_training')
    from django.db.models import Exists, OuterRef, Q
    docs = PrivateDocument.objects.filter(training_id=OuterRef('pk'), employee_id=OuterRef('employee_id'), active=True, kind='certificate')
    from django.db.models import Case,When,Value,BooleanField
    rows = Training.objects.filter(status='Concluído').select_related('employee', 'group').annotate(has_new_pdf=Exists(docs))
    rows = rows.annotate(has_certificate_pdf=Case(When(Q(has_new_pdf=True)|Q(source_data___EPAL_CONSOLIDATED_FACTS__archived_pdf_ids__0__isnull=False),then=Value(True)),default=Value(False),output_field=BooleanField()))
    q = request.GET.get('q', '').strip()
    if q:
        rows = rows.filter(Q(employee_id__in=person_ids(q)) | Q(title__icontains=q))
    if request.GET.get('state') == 'pending':
        rows = rows.filter(has_certificate_pdf=False)
    if request.GET.get('state') == 'archived':
        rows = rows.filter(has_certificate_pdf=True)
    from django.core.paginator import Paginator
    page = Paginator(rows.order_by('employee__name', 'pk'), 50).get_page(request.GET.get('page'))
    return render(request, 'certificate_archive.html', {'page': page, 'q': q, 'state': request.GET.get('state', '')})
MAX_PDF_BYTES = 10 * 1024 * 1024

def authorised(user, permission):
    return user.is_authenticated and user.is_active and user.is_staff and user.has_perm('training.' + permission)

def require(user, permission):
    if not getattr(settings, 'PMF_DOCUMENTS_ENABLED', False) or not authorised(user, permission):
        raise PermissionDenied

def audit(user, action, payload):
    return AuditEvent.objects.create(actor=user.get_username(), action=action, detail=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))

def validate_pdf(upload):
    if Path(upload.name).suffix.lower() != '.pdf' or upload.content_type != 'application/pdf':
        raise ValidationError('Carregue exclusivamente um ficheiro PDF.')
    data = upload.read(MAX_PDF_BYTES + 1)
    if not data or len(data) > MAX_PDF_BYTES:
        raise ValidationError('O limite é 10 MiB por PDF.')
    if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]:
        raise ValidationError('Estrutura PDF inválida ou incompleta.')
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
            raise ValueError('encrypted or page limit')
        forbidden = {'/JS', '/JavaScript', '/Launch', '/EmbeddedFiles', '/OpenAction', '/AA', '/XFA', '/RichMedia'}
        visited = set()

        def inspect(obj, depth=0):
            if depth > 50:
                raise ValueError('object nesting')
            obj = obj.get_object() if hasattr(obj, 'get_object') else obj
            if id(obj) in visited:
                return
            visited.add(id(obj))
            if isinstance(obj, dict):
                if forbidden.intersection(obj):
                    raise ValueError('active content')
                for value in obj.values():
                    inspect(value, depth + 1)
            elif isinstance(obj, (list, tuple)):
                for value in obj:
                    inspect(value, depth + 1)
        inspect(reader.trailer)
    except Exception as exc:
        raise ValidationError('PDF ilegível, protegido, demasiado complexo ou com conteúdo activo.') from exc
    return data

class UploadForm(forms.Form):
    kind = forms.ChoiceField(label='Tipo', choices=PrivateDocument._meta.get_field('kind').choices)
    training = forms.ModelChoiceField(label='Formação (obrigatória para certificado)', queryset=Training.objects.none(), required=False)
    pdf = forms.FileField(label='PDF (máximo 10 MiB)', widget=forms.ClearableFileInput(attrs={'accept': '.pdf,application/pdf'}))
    reference = forms.CharField(label='Referência documental', max_length=500)

    def __init__(self, *args, employee, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.fields['training'].queryset = employee.trainings.all()

    def clean_pdf(self):
        upload = self.cleaned_data['pdf']
        self.pdf_bytes = validate_pdf(upload)
        return upload

    def clean(self):
        data = super().clean()
        if (data.get('kind') == 'certificate') != bool(data.get('training')):
            self.add_error('training', 'Associe certificados à formação; um CV não deve ter formação associada.')
        return data

@transaction.atomic
def store_document(user, employee, form):
    require(user, 'add_privatedocument')
    if not form.is_valid() or form.employee.pk != employee.pk:
        raise ValidationError('Proposta de documento inválida.')
    data = form.pdf_bytes
    doc = PrivateDocument(employee=employee, training=form.cleaned_data['training'], kind=form.cleaned_data['kind'], content=data, sha256=hashlib.sha256(data).hexdigest(), size=len(data), actor=user.get_username(), reference=form.cleaned_data['reference'])
    doc.full_clean()
    doc.save()
    audit(user, 'PRIVATE_DOCUMENT_UPLOAD', {'id': str(doc.pk), 'employee_id': employee.pk, 'training_id': doc.training_id, 'kind': doc.kind, 'sha256': doc.sha256, 'size': doc.size, 'reference': doc.reference})
    return doc

class DeliveryForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    justification = forms.CharField(label='Justificação', widget=forms.Textarea, max_length=2000)
    confirm = forms.BooleanField(label='Confirmo os estados e a fundamentação documental')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('issued', 'learner_delivered', 'hr_delivered'):
            label = self.fields[name].label
            self.fields[name] = forms.TypedChoiceField(label=label, required=False, choices=[('unknown', 'Por confirmar'), ('true', 'Sim'), ('false', 'Não')], empty_value=None, coerce=lambda value: {'unknown': None, 'true': True, 'false': False}[value])
            value = self.initial.get(name)
            self.initial[name] = 'true' if value is True else 'false' if value is False else 'unknown'

    class Meta:
        model = TrainingDocumentState
        fields = ['issued', 'learner_delivered', 'learner_date', 'learner_responsible', 'hr_delivered', 'hr_date', 'hr_responsible', 'observations', 'reference']
        widgets = {key: forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d') for key in ('learner_date', 'hr_date')}

@transaction.atomic
def save_delivery(user, training, data):
    require(user, 'change_trainingdocumentstate')
    pending = audit(user, 'DOCUMENT_STATE_PENDING', {'training_id': training.pk})
    training = Training.objects.get(pk=training.pk)
    state = TrainingDocumentState.objects.filter(training=training).first()
    before = model_to_dict(state) if state else None
    revision = state.revision if state else 0
    form = DeliveryForm(data, instance=state or TrainingDocumentState(training=training))
    if not form.is_valid():
        raise ValidationError(form.errors.as_json())
    if form.cleaned_data['revision'] != revision:
        raise ValidationError('O estado mudou noutra janela. Reabra a página.')
    state = form.save(commit=False)
    state.revision = revision + 1
    state.full_clean()
    state.save()
    pending.action = 'DOCUMENT_STATE_CHANGE'
    pending.detail = json.dumps({'training_id': training.pk, 'before': before, 'after': model_to_dict(state), 'justification': form.cleaned_data['justification']}, ensure_ascii=False, default=str)
    pending.save(update_fields=['action', 'detail'])
    return state

class ProfessionalForm(forms.Form):
    category = forms.CharField(label='Categoria profissional', max_length=200, required=False)
    function = forms.CharField(label='Função exercida', max_length=200, required=False)
    revision = forms.CharField(widget=forms.HiddenInput)
    justification = forms.CharField(label='Justificação', max_length=2000, widget=forms.Textarea)
    reference = forms.CharField(label='Referência documental', max_length=500)
    confirm = forms.BooleanField(label='Confirmo a correcção local; não representa aprovação oficial de RH')

def professional_revision(employee):
    last = AuditEvent.objects.filter(action='PROFESSIONAL_CORRECTION', detail__contains=f'"employee_id": {employee.pk},').order_by('-pk').values_list('pk', flat=True).first()
    return hashlib.sha256(json.dumps([employee.category, employee.function, last], ensure_ascii=False).encode()).hexdigest()

@transaction.atomic
def correct_professional(user, employee_id, data):
    require(user, 'change_employee')
    pending = audit(user, 'PROFESSIONAL_PENDING', {'employee_id': employee_id})
    employee = Employee.objects.get(pk=employee_id)
    form = ProfessionalForm(data)
    if not form.is_valid() or form.cleaned_data['revision'] != professional_revision(employee):
        raise ValidationError('Dados inválidos ou alterados noutra janela. Reabra a página.')
    before = {'category': employee.category, 'function': employee.function}
    after = {key: form.cleaned_data[key] for key in before}
    if before == after:
        raise ValidationError('Não existem alterações propostas.')
    if Employee.objects.filter(pk=employee.pk, **before).update(**after) != 1:
        raise ValidationError('Alteração concorrente.')
    pending.action = 'PROFESSIONAL_CORRECTION'
    pending.detail = json.dumps({'employee_id': employee.pk, 'before': before, 'after': after, 'justification': form.cleaned_data['justification'], 'reference': form.cleaned_data['reference']}, ensure_ascii=False)
    pending.save(update_fields=['action', 'detail'])

def delivery_summary(training):
    from .data_validation import enabled, fact, display, display_state
    if enabled():
        training = display(training)
    state = display_state(training, getattr(training, 'document_state', None))
    from .source_achievement import achievement
    from .analytics import document_value
    label = lambda v: 'Sim' if v is True else 'Não' if v is False else 'Por confirmar'
    attendance_known = training.attendance_confirmed and training.attendance is not None
    participation = ('Comprovada' if training.attendance > 0 else 'Ausência confirmada') if attendance_known else 'Participação sem prova suficiente'
    return {'training': training, 'grade': training.grade, 'grade_status': 'Registada' if training.grade is not None else 'Sem nota registada', 'participation': participation, 'attendance_known': attendance_known, 'issued': label(document_value(training,'issued')), 'learner': label(document_value(training,'learner_delivered')), 'hr': label(document_value(training,'hr_delivered')), 'state': state, 'verified_absences': training.absences if attendance_known else None, 'achievement': getattr(training, 'source_achievement', None) or achievement(training.original if enabled() else training) if fact(training, 'evaluation') else None}

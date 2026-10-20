"""Private administrative archive and transactional corrections; no Training updates."""
import hashlib
import io
import json
from pathlib import Path
from pypdf import PdfReader
from django import forms
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from .models import AuditEvent, Employee, PrivateDocument, Training, TrainingDocumentState

MAX_PDF_BYTES = 10 * 1024 * 1024


def authorised(user, permission):
    return user.is_authenticated and user.is_active and user.is_staff and user.has_perm('training.' + permission)


def require(user, permission):
    if not getattr(settings, 'PMF_DOCUMENTS_ENABLED', False) or not authorised(user, permission):
        raise PermissionDenied


def audit(user, action, payload):
    return AuditEvent.objects.create(actor=user.get_username(), action=action,
        detail=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


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
    doc = PrivateDocument(employee=employee, training=form.cleaned_data['training'], kind=form.cleaned_data['kind'],
        content=data, sha256=hashlib.sha256(data).hexdigest(), size=len(data), actor=user.get_username(), reference=form.cleaned_data['reference'])
    doc.full_clean();doc.save()
    audit(user, 'PRIVATE_DOCUMENT_UPLOAD', {'id':str(doc.pk), 'employee_id':employee.pk,
        'training_id':doc.training_id, 'kind':doc.kind, 'sha256':doc.sha256, 'size':doc.size, 'reference':doc.reference})
    return doc


class DeliveryForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    justification = forms.CharField(label='Justificação', widget=forms.Textarea, max_length=2000)
    confirm = forms.BooleanField(label='Confirmo os estados e a fundamentação documental')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('issued','learner_delivered','hr_delivered'):
            label = self.fields[name].label
            self.fields[name] = forms.TypedChoiceField(label=label, required=False,
                choices=[('unknown','Por confirmar'),('true','Sim'),('false','Não')], empty_value=None,
                coerce=lambda value: {'unknown':None,'true':True,'false':False}[value])
            value = self.initial.get(name)
            self.initial[name] = 'true' if value is True else 'false' if value is False else 'unknown'
    class Meta:
        model = TrainingDocumentState
        fields = ['issued', 'learner_delivered', 'learner_date', 'learner_responsible', 'hr_delivered', 'hr_date', 'hr_responsible', 'observations', 'reference']
        widgets = {key: forms.DateInput(attrs={'type':'date'}, format='%Y-%m-%d') for key in ('learner_date','hr_date')}


@transaction.atomic
def save_delivery(user, training, data):
    require(user, 'change_trainingdocumentstate')
    pending = audit(user, 'DOCUMENT_STATE_PENDING', {'training_id':training.pk})
    training = Training.objects.get(pk=training.pk)
    state = TrainingDocumentState.objects.filter(training=training).first()
    before = model_to_dict(state) if state else None
    revision = state.revision if state else 0
    form = DeliveryForm(data, instance=state or TrainingDocumentState(training=training))
    if not form.is_valid():
        raise ValidationError(form.errors.as_json())
    if form.cleaned_data['revision'] != revision:
        raise ValidationError('O estado mudou noutra janela. Reabra a página.')
    state = form.save(commit=False);state.revision = revision + 1;state.full_clean();state.save()
    # Pending marker is updated rather than left as a second business event.
    pending.action = 'DOCUMENT_STATE_CHANGE'
    pending.detail = json.dumps({'training_id':training.pk, 'before':before, 'after':model_to_dict(state),
        'justification':form.cleaned_data['justification']}, ensure_ascii=False, default=str)
    pending.save(update_fields=['action','detail'])
    return state


class ProfessionalForm(forms.Form):
    category = forms.CharField(label='Categoria profissional', max_length=200, required=False)
    function = forms.CharField(label='Função exercida', max_length=200, required=False)
    revision = forms.CharField(widget=forms.HiddenInput)
    justification = forms.CharField(label='Justificação', max_length=2000, widget=forms.Textarea)
    reference = forms.CharField(label='Referência documental', max_length=500)
    confirm = forms.BooleanField(label='Confirmo a correcção local; não representa aprovação oficial de RH')


def professional_revision(employee):
    last = AuditEvent.objects.filter(action='PROFESSIONAL_CORRECTION',
        detail__contains=f'"employee_id": {employee.pk},').order_by('-pk').values_list('pk', flat=True).first()
    return hashlib.sha256(json.dumps([employee.category, employee.function, last], ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def correct_professional(user, employee_id, data):
    require(user, 'change_employee')
    pending = audit(user, 'PROFESSIONAL_PENDING', {'employee_id':employee_id})
    employee = Employee.objects.get(pk=employee_id)
    form = ProfessionalForm(data)
    if not form.is_valid() or form.cleaned_data['revision'] != professional_revision(employee):
        raise ValidationError('Dados inválidos ou alterados noutra janela. Reabra a página.')
    before = {'category':employee.category, 'function':employee.function}
    after = {key:form.cleaned_data[key] for key in before}
    if before == after:
        raise ValidationError('Não existem alterações propostas.')
    if Employee.objects.filter(pk=employee.pk, **before).update(**after) != 1:
        raise ValidationError('Alteração concorrente.')
    pending.action = 'PROFESSIONAL_CORRECTION'
    pending.detail = json.dumps({'employee_id':employee.pk, 'before':before,'after':after,
        'justification':form.cleaned_data['justification'],'reference':form.cleaned_data['reference']}, ensure_ascii=False)
    pending.save(update_fields=['action','detail'])


def delivery_summary(training):
    state = getattr(training,'document_state',None)
    from .source_achievement import achievement
    label = lambda v: 'Sim' if v is True else 'Não' if v is False else 'Por confirmar'
    return {'training':training, 'grade':training.grade, 'grade_status':'Registada' if training.grade is not None else 'Por confirmar',
        'participation':'Comprovada' if training.attendance_confirmed and training.attendance > 0 else 'Ausência confirmada' if training.attendance_confirmed else 'Por confirmar',
          'issued':label(state.issued if state else None), 'learner':label(state.learner_delivered if state and state.learner_delivered is not None else training.certified),
          'hr':label(state.hr_delivered if state else None), 'state':state,'verified_absences':training.absences if training.attendance_confirmed else None,'achievement':getattr(training,'source_achievement',None) or achievement(training)}

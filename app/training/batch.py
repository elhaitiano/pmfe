"""Review and atomically update the participants of one training edition."""
import hashlib
import json
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Min, Q
from django.forms import BaseModelFormSet, modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from .forms import TrainingForm
from .models import Training
from .views import record, staff_required

EDITION_FIELDS = ('title', 'area', 'start', 'end', 'trainer', 'provider',
                  'location', 'days', 'hours_per_day', 'group_id')
UPDATE_FIELDS = ('status', 'attendance', 'attendance_confirmed', 'grade', 'result', 'certified',
                 'certificate_delivery_date', 'certificate_reference')


class BatchForm(TrainingForm):
    revision = forms.IntegerField(widget=forms.HiddenInput, required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['revision'] = self.instance.version
        if self.instance.pk:
            person = self.instance.employee
            for field in UPDATE_FIELDS:
                self.fields[field].widget.attrs['aria-label'] = f'{self.fields[field].label} — {person.name} ({person.number})'

    class Meta(TrainingForm.Meta):
        fields = UPDATE_FIELDS


class EditionFormSet(BaseModelFormSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_ids = set(self.get_queryset().values_list('pk', flat=True))

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        identifiers = [f.instance.pk for f in self.forms]
        if len(identifiers) != len(self.allowed_ids) or set(identifiers) != self.allowed_ids:
            raise ValidationError('A lista de participações mudou ou está incompleta. Reabra a grelha.')


BatchFormSet = modelformset_factory(Training, form=BatchForm, formset=EditionFormSet,
    extra=0, max_num=1000, validate_max=True, absolute_max=1000)


def edition_queryset(seed):
    return Training.objects.filter(**{key: getattr(seed, key) for key in EDITION_FIELDS})\
        .select_related('employee').order_by('employee__name', 'employee__number', 'pk')


def payload_digest(formset):
    data = [{'id': f.instance.pk, 'revision': f.cleaned_data['revision'],
             **{key: f.cleaned_data[key] for key in UPDATE_FIELDS}} for f in formset]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


@login_required
@staff_required
def batch_index(request):
    query = request.GET.get('q', '').strip()
    rows = Training.objects.all()
    if query:
        rows = rows.filter(Q(title__icontains=query) | Q(trainer__icontains=query)
                           | Q(location__icontains=query) | Q(provider__icontains=query))
    editions = rows.values(*EDITION_FIELDS, 'group__number').annotate(seed=Min('pk'), participants=Count('pk'))\
        .order_by('-start', 'title', 'seed')
    return render(request, 'batch_index.html', {'page': Paginator(editions, 20).get_page(request.GET.get('page')),
        'q': query})


@login_required
@staff_required
def batch_edit(request, pk):
    seed = get_object_or_404(Training, pk=pk)
    queryset = edition_queryset(seed)
    if queryset.count() > 1000:
        messages.error(request, 'Esta edição tem mais de 1.000 participações. Utilize a edição individual.')
        return redirect('batch_index')
    # Capture originals before ModelForm validation changes its instances in memory.
    before = {t.pk: {key: getattr(t, key) for key in UPDATE_FIELDS} for t in queryset}
    versions = {t.pk: t.version for t in queryset}
    formset = BatchFormSet(request.POST if request.method == 'POST' else None,
                           queryset=queryset, prefix='participants')
    review = False
    token = ''
    modified = []
    error = ''
    if request.method == 'POST' and formset.is_valid():
        if any(f.cleaned_data['revision'] != versions.get(f.instance.pk) for f in formset):
            error = 'Há alterações mais recentes neste grupo. Reabra a grelha para evitar substituir dados atualizados.'
        else:
            modified = [f for f in formset if any(f.cleaned_data[k] != before[f.instance.pk][k] for k in UPDATE_FIELDS)]
            digest = payload_digest(formset)
            action = request.POST.get('action')
            if action == 'review':
                review = True
                token = signing.dumps({'user': request.user.pk, 'seed': pk, 'digest': digest}, salt='batch-review')
            elif action == 'confirm':
                try:
                    reviewed = signing.loads(request.POST.get('review_token', ''), salt='batch-review', max_age=1200)
                    if reviewed != {'user': request.user.pk, 'seed': pk, 'digest': digest}:
                        raise signing.BadSignature('Changed payload')
                    with transaction.atomic():
                        for form in modified:
                            ident = form.instance.pk
                            values = {key: form.cleaned_data[key] for key in UPDATE_FIELDS}
                            changed = Training.objects.filter(pk=ident, version=form.cleaned_data['revision']).update(
                                **values, version=form.cleaned_data['revision'] + 1)
                            if changed != 1:
                                raise ValidationError('Uma participação foi alterada noutra janela. Nenhuma alteração deste lote foi guardada. Reabra a grelha.')
                            detail = json.dumps({'training': ident, 'employee': form.instance.employee.number,
                                'edition': seed.title, 'before': before[ident], 'after': values}, ensure_ascii=False, default=str)
                            record(request, 'BATCH_UPDATE_TRAINING', detail)
                    messages.success(request, f'{len(modified)} participação(ões) atualizada(s). Os indicadores e as fichas já refletem os novos dados.')
                    return redirect('batch_edit', pk=pk)
                except (signing.BadSignature, signing.SignatureExpired):
                    error = 'A revisão expirou ou os valores foram alterados. Reveja novamente antes de guardar.'
                except ValidationError as exc:
                    error = ' '.join(exc.messages)
            elif action != 'edit':
                error = 'Reveja as alterações antes de guardar.'
    changed_ids = {f.instance.pk for f in modified}
    display_rows = list(queryset)
    for index, form in enumerate(formset):
        form.display_employee = display_rows[index].employee if index < len(display_rows) else None
        form.is_modified = form.instance.pk in changed_ids
    return render(request, 'batch_edit.html', {'seed': seed, 'formset': formset,
        'review': review, 'review_token': token, 'modified_count': len(modified), 'batch_error': error})

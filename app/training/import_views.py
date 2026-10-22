"""Superuser-only Excel review. There is deliberately no web apply action."""
import hashlib
from io import BytesIO

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .excel_sources import load_source, SOURCE_ERRORS
from .integrated_import import build_plan


class PreviewForm(forms.Form):
    workbook = forms.FileField(label='Ficheiro Excel (.xlsx ou .xlsm)',
                               widget=forms.ClearableFileInput(attrs={'accept': '.xlsx,.xlsm'}))

    def clean_workbook(self):
        upload = self.cleaned_data['workbook']
        if upload.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Tamanho máximo: 10 MB.')
        if not upload.name.lower().endswith(('.xlsx', '.xlsm')):
            raise forms.ValidationError('Seleccione um ficheiro .xlsx ou .xlsm.')
        return upload


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def preview(request):
    if not (request.user.is_superuser and request.user.is_staff and request.user.is_active):
        raise PermissionDenied
    form = PreviewForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == 'POST' and form.is_valid():
        workbook = None
        try:
            data = form.cleaned_data['workbook'].read()
            workbook = load_source(BytesIO(data))
            with transaction.atomic():
                report = build_plan(workbook, hashlib.sha256(data).hexdigest()).report
        except SOURCE_ERRORS as error:
            form.add_error('workbook', f'Não foi possível analisar o Excel: {error}')
        finally:
            if workbook:
                workbook.close()
        if report and request.POST.get('output') == 'json':
            response = JsonResponse(report, json_dumps_params={'ensure_ascii': False, 'indent': 2})
            response['Content-Disposition'] = 'attachment; filename="importacao-preview.json"'
            return response
    return render(request, 'import_preview.html', {'form': form, 'report': report})

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_GET

from . import catalogue_corrections as service
from .correction_forms import CorrectionForm, DocumentaryForm, ConfirmationForm
from .models import Course, AuditEvent


def handle(request, pk, event_pk=None):
    service.authorise(request.user)
    course = get_object_or_404(Course, pk=pk)
    if not service.in_scope(course):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('GIRH e outras acções permanecem fora do âmbito aprovado.')
    original = get_object_or_404(AuditEvent, pk=event_pk, action=service.ACTION) if event_pk else None
    if original:
        import json
        if json.loads(original.detail)['course_id'] != course.pk:
            return HttpResponseBadRequest('Evento de outra acção.')
    mode = 'reverse' if original else 'correct'
    form_class = DocumentaryForm if original else CorrectionForm
    rule = service.RULES[course.source_action_id]
    initial = {'revision':course.revision, 'days':rule['days'], 'hours':Decimal(rule['hours']),
               'correction_type':'documented_standard'}
    form = form_class(initial=initial)
    preview = confirmation = None
    status = 200
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'review':
            form = form_class(request.POST)
            if form.is_valid():
                try:
                    preview, token = service.package(course, request.user, form.cleaned_data, mode, original)
                    confirmation = ConfirmationForm(initial={'token':token})
                except ValidationError as exc:
                    form.add_error(None, exc)
            if form.errors:
                status = 400
        elif action == 'confirm':
            confirmation = ConfirmationForm(request.POST)
            if confirmation.is_valid():
                try:
                    event = service.confirm(confirmation.cleaned_data['token'], request.user,
                                            course, mode, event_pk)
                    messages.success(request, 'Operação confirmada e auditada; histórico preservado.')
                    return redirect('course_correction_result', pk=course.pk, event_pk=event.pk)
                except (ValidationError, signing.BadSignature) as exc:
                    confirmation.add_error(None, str(exc))
                    status = 409
            else:
                status = 400
        else:
            return HttpResponseBadRequest('Operação inválida.')
    return render(request, 'planning/correction.html', {'course':course, 'form':form,
        'confirmation':confirmation, 'preview':preview, 'reverse':bool(original),
        'history':service.correction_history(course), 'documentary_warning':rule['warning']}, status=status)


@login_required
@require_http_methods(['GET','POST'])
def correct(request, pk):
    return handle(request, pk)


@login_required
@require_http_methods(['GET','POST'])
def reverse(request, pk, event_pk):
    return handle(request, pk, event_pk)


@login_required
@require_GET
def result(request, pk, event_pk):
    service.authorise(request.user)
    course = get_object_or_404(Course, pk=pk)
    if not service.in_scope(course):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    entry = next((item for item in service.correction_history(course) if item['event'].pk == event_pk), None)
    if entry is None:
        from django.http import Http404
        raise Http404
    return render(request, 'planning/correction_result.html', {'course':course, 'entry':entry})

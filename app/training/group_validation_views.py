"""One preview/confirmation flow, embedded in existing group management."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count,Q
from django.http import HttpResponseBadRequest
from django.shortcuts import render,redirect
from django.urls import reverse
from .edit_navigation import saved_response
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from . import group_validation as service
from .models import ConsolidatedEdition,TrainingGroup


@login_required
@require_http_methods(['GET','POST'])
@never_cache
def review(request):
    service.authorize(request.user)
    # This additive transaction is fully managed by the service. Do not run the
    # unrelated global historical reconciliation middleware on this POST.
    request.group_validation_managed=True
    keys=request.GET.getlist('group') if request.method=='GET' else request.POST.getlist('group')
    error=None; status=200; data={}
    if request.method=='POST':
        if set(request.POST)-{'csrfmiddlewaretoken','token','confirm','action'}:
            return HttpResponseBadRequest('Campos não autorizados na confirmação.')
        if request.POST.get('action')!='confirm':return HttpResponseBadRequest('Acção inválida.')
        try:
            changed=service.apply(request.user,request.POST.get('token'),request.POST.get('confirm')=='yes')
        except ValidationError as exc:
            error=' '.join(exc.messages);status=409
            # No unsigned or altered groups are accepted after a failed confirmation.
        else:
            messages.success(request,f'Validação concluída: {len(changed)} participação(ões). Notas e certificados preservados.')
            return saved_response(request,reverse('group_validation'))
    elif keys or request.GET.get('course'):
        try:
            if keys and request.GET.get('course'):raise ValidationError('Escolha grupos ou uma formação, não ambos.')
            data=service.preview(request.user,keys or None,course_id=request.GET.get('course') or None)
        except ValidationError as exc:error=' '.join(exc.messages);status=400
    editions=ConsolidatedEdition.objects.filter(active=True).select_related('course').annotate(
        member_count=Count('participations',filter=Q(participations__active=True))).order_by('-start','pk')
    native=TrainingGroup.objects.select_related('course').order_by('-start','pk')
    q=request.GET.get('q','').strip()[:150]
    if q:
        editions=editions.filter(Q(title__icontains=q)|Q(group_label__icontains=q)|Q(course__code__icontains=q))
        native=native.filter(Q(title__icontains=q)|Q(course__code__icontains=q))
    # Bound the selector and require a filter rather than generating thousands of rows.
    data.update(error=error,editions=editions[:100],native_groups=native[:100],q=q,
                selection_limit=service.MAX_GROUPS,can_correct=request.user.has_perm('training.change_training'))
    return render(request,'planning/group_validation.html',data,status=status)

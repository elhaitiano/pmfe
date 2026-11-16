"""Administração e Definições: entry page for the administrative tools.

Adapted from the isolated 8088 version (ux_presentation.administration). Each
button is shown only when the user is allowed AND its destination route exists
in this installation; visibility never replaces the destination's own checks.
"""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_GET


def _url(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def tools_for(user):
    """Buttons in the order of the reference screen, filtered by access and routes."""
    if not (user and user.is_authenticated and user.is_active):
        return []
    staff, superuser = user.is_staff, user.is_superuser
    can_validate = user.has_perms(['training.validate_training_group', 'training.view_training', 'training.view_employee'])
    can_audit = staff and user.has_perm('training.view_auditevent')
    can_document = staff and (user.has_perm('training.add_privatedocument')
                              or user.has_perm('training.change_trainingdocumentstate'))
    candidates = [
        (staff and not can_audit, 'audit', 'Registo de actividade', False),
        (can_validate, 'group_validation', 'Validação de grupos', True),
        (can_audit, 'audit', 'Auditoria', False),
        (can_document, 'employee_data_select', 'Documentação dos formandos', False),
        (superuser, 'historical_exceptions', 'Pendências e divergências', False),
        (superuser, 'historical_regularization_report', 'Regularização documental', False),
        (superuser, 'admin:index', 'Contas e manutenção', False),
    ]
    buttons = []
    for allowed, route, label, primary in candidates:
        url = _url(route) if allowed else None
        if url:
            buttons.append({'route': route, 'label': label, 'url': url, 'primary': primary})
    return buttons


def navigation(request):
    """Context processor: controls the "Administração e Definições" menu entry."""
    return {'ux_admin_visible': bool(tools_for(getattr(request, 'user', None)))}


def calculation_criteria(request):
    """Calculation criteria of the dashboard's default view (global, all periods).

    Same selection and scope as the Dashboard Executivo opened without filters,
    so the explanation matches the figures shown there.
    """
    from .analytics import statistics
    from .executive import selection
    from .kpi_service import Scope
    form, selected, start, end, label, rows, employees, base, valid = selection(request)
    basis = 'training_start' if selected.get('time_basis') == 'training_start' else 'session_date'
    stats = statistics(rows, employees, Scope(selected, label, basis, 'current_registry', start, end))
    return {'stats': stats, 'kpi_summary': stats['kpi_summary'], 'kpi_details': stats['kpi_details']}


@login_required
@require_GET
def tools(request):
    buttons = tools_for(request.user)
    if not buttons:
        raise PermissionDenied
    return render(request, 'ux_administration.html', {'tools': buttons, **calculation_criteria(request)})

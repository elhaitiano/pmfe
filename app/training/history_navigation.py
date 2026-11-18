"""Read-only entry to the existing individual history; no historical projection changes."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET
from . import data_validation as validation
from .models import Employee
from .professional_ui import person_ids


@login_required
@require_GET
def index(request):
    selected = request.GET.get('employee', '')
    if selected.isdecimal() and len(selected) < 18:
        # The existing destination keeps its original profile-access checks.
        return redirect('employee_detail', pk=int(selected))
    people = validation.worker_queryset(Employee.objects.all()).order_by('name', 'number')
    query = request.GET.get('q', '').strip()
    if query:
        people = people.filter(pk__in=person_ids(query))
    return render(request, 'history_index.html', {
        'history_query': query,
        'page': Paginator(people, 30).get_page(request.GET.get('page')),
    })

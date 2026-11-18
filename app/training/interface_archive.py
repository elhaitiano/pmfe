from .training_areas import AREAS
"""Read-only access to valid older employees outside the current official roster."""
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404,render
from .models import Employee,Training
from .analytics import statistics,profile_fields


def employee(request,pk):
    if not request.user.is_staff:raise PermissionDenied
    person=get_object_or_404(Employee.all_objects,pk=pk,registry_active=False)
    rows=list(Training.all_objects.filter(employee=person).order_by('-start','pk'))
    return render(request,'employee.html',{'employee':person,'trainings':rows,'stats':statistics(rows),
        'years':sorted({t.start.year for t in rows},reverse=True),'areas':list(AREAS),
        'archived_roster':True,'latest_location':rows[0].location if rows else ''})

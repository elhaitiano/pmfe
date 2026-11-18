from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .models import TrainingAreaIssue, Course, Training
from .views import staff_required
from .training_areas import AREAS


@login_required
@staff_required
@require_GET
def pending(request):
    issues=list(TrainingAreaIssue.objects.filter(resolved_at__isnull=True).order_by('title'))
    course_ids=[]
    for issue in issues:
        issue.course=Course.objects.filter(pk=issue.object_id).first() if issue.model_name=='Course' else None
        rows=Training.objects.filter(course=issue.course) if issue.course else Training.objects.none()
        issue.participations=rows.count();issue.people=rows.values('employee_id').distinct().count()
        if issue.course:course_ids.append(issue.course.pk)
    affected=Training.objects.filter(course_id__in=course_ids)
    return render(request,'planning/area_issues.html',{'issues':issues,'area_categories':AREAS,'affected_participations':affected.count(),'affected_people':affected.values('employee_id').distinct().count()})

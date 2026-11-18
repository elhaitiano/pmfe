from types import SimpleNamespace
from django.core.paginator import Paginator
from django.db.models import Count,Q
from django.shortcuts import get_object_or_404,render
from .models import ConsolidatedEdition,HistoricalInterfaceLink
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from .views import staff_required
import io
import re


def group_code(label):
    literal=str(label or '').strip();match=re.fullmatch(r'G?([0-9]+)',literal,re.I)
    return 'G'+match.group(1) if match else literal or 'Grupo não indicado'


def attach_course_counts(courses):
    courses=list(courses)
    represented=HistoricalInterfaceLink.objects.filter(training__group_id__isnull=False).values_list('participation__edition_id',flat=True)
    counts=dict(ConsolidatedEdition.objects.filter(active=True,course_id__in=[c.pk for c in courses]).exclude(pk__in=represented).values('course_id').annotate(n=Count('pk')).values_list('course_id','n'))
    for course in courses:course.group_count+=counts.get(course.pk,0)
    return courses

def extend(context,request):
    editions=ConsolidatedEdition.objects.filter(active=True).annotate(participant_count=Count('participations',filter=Q(participations__active=True)))
    represented=HistoricalInterfaceLink.objects.filter(training__group_id__isnull=False).values_list('participation__edition_id',flat=True)
    editions=editions.exclude(pk__in=represented)
    if context.get('selected_course'):editions=editions.filter(course=context['selected_course'])
    q=context.get('q')
    if q:editions=editions.filter(Q(title__icontains=q)|Q(trainer__icontains=q)|Q(location__icontains=q)|Q(course_reference__icontains=q))
    native=list(context['page'].paginator.object_list)
    from .group_roster import summaries as roster_summaries
    summaries=roster_summaries(native)
    for group in native:
        group.participant_count=summaries[group.pk]['participant_count'];group.display_state=summaries[group.pk]['display_state'];group.historical_only=summaries[group.pk]['historical_only']
    for e in editions:
        e.code=group_code(e.group_label)
        e.days=e.declared_days;e.display_state='Histórico';e.historical_only=True;e.historical_edition=True
        native.append(e)
    native.sort(key=lambda g:(g.start,g.pk),reverse=True)
    context['page']=Paginator(native,20).get_page(request.GET.get('page'))
    context['courses']=attach_course_counts(context['courses'])
    context['historical_count']=editions.count()
    context['planning_summary']['total']+=context['historical_count']
    return context


def detail(request,pk):
    e=get_object_or_404(ConsolidatedEdition,pk=pk,active=True)
    links=HistoricalInterfaceLink.objects.filter(participation__edition=e,participation__active=True).select_related('training','training__employee')
    members=[SimpleNamespace(employee=l.training.employee,training=l.training) for l in links]
    e.code=group_code(e.group_label);e.days=e.declared_days
    roster={'members':members,'historical_only':True,'display_state':'Histórico','completed_count':sum(m.training.status=='Concluído' for m in members),
        'verified_present_count':sum(bool(m.training.attendance_confirmed and m.training.attendance and m.training.attendance>0) for m in members),
        'attendance_pending_count':sum(not m.training.attendance_confirmed for m in members)}
    return render(request,'planning/group.html',{'group':e,'roster':roster,'participant_count':len(members),'attendance_open':False,
        'seed':members[0].training if members else None,'historical_edition':True})


@login_required
@staff_required
def attendance(request,pk,kind='pdf'):
    e=get_object_or_404(ConsolidatedEdition,pk=pk,active=True);e.code='G'+e.group_label if e.group_label else 'Grupo não indicado'
    members=[SimpleNamespace(employee=l.training.employee) for l in HistoricalInterfaceLink.objects.filter(participation__edition=e,participation__active=True).select_related('training__employee')]
    period=f'{e.start:%d/%m/%Y} - {e.end:%d/%m/%Y}'
    if kind=='xlsx':
        from .attendance_excel import build_attendance_excel
        content=build_attendance_excel(e,None,members,second_copy=True,historical_period=period)
    else:
        from .attendance_pdf import build_attendance
        content=build_attendance(e,None,members,second_copy=True,historical_period=period)
    return FileResponse(io.BytesIO(content),as_attachment=kind=='xlsx',filename=f'EPAL_lista_historica_{pk}.{kind}',content_type='application/pdf' if kind=='pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

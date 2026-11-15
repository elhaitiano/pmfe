"""Daily evidence is authoritative where present; legacy totals are never distributed."""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from django.utils import timezone
from .models import GroupSession, SessionAttendance
from .kpi_service import aggregate_attendance, _summarize

def attendance_summary(rows, employees, *, scope):
    rows=list(rows); employees=list(employees)
    if scope.time_basis in ('training_start','training_overlap'):
        return aggregate_attendance(rows,employees,scope=scope)
    today=timezone.localdate()
    start,end=scope.date_from,scope.date_to
    groups={r.group_id for r in rows if r.group_id}
    sessions=defaultdict(list)
    for session in GroupSession.objects.filter(group_id__in=groups):
        sessions[session.group_id].append(session)
    attendance=defaultdict(dict)
    for item in SessionAttendance.objects.filter(training_id__in=[r.pk for r in rows]).select_related('session'):
        attendance[item.training_id][item.session_id]=item
    unique={};positive={};known=set();hours={};pairs={};issues=defaultdict(set)
    exclusions={key:defaultdict(set) for key in ('counts','hours','attendance')}
    inconsistent=set();planned=set();day_pairs=set();dated=defaultdict(Decimal);unallocated=0
    for row in {r.pk:r for r in rows}.values():
        daily=attendance.get(row.pk,{})
        if not daily:
            # Legacy is readable globally, but cannot contribute fictitious daily hours.
            overlaps=(not start or row.end>=start) and (not end or row.start<=end)
            if (start or end) and not overlaps:
                continue
            unique[row.pk]=row
            unallocated+=1
            if start or end:
                for bucket in exclusions.values():bucket[row.pk].add('legacy_without_daily_dates')
                continue
            legacy=aggregate_attendance([row],employees,scope=replace(scope,time_basis='all_records'))
            hours.update(legacy.hours_by_participation);pairs.update(legacy.attendance_pairs)
            known.update(legacy.confirmed_participations)
            if row.pk in legacy.effective_participations:positive[row.pk]=row.employee_id
            for bucket,key in [('counts','people'),('hours','hours'),('attendance','attendance')]:
                metric=legacy.metrics[key]
                for reason in metric.reasons:exclusions[bucket][row.pk].add(reason)
                if metric.status=='inconsistent':inconsistent.add(row.pk)
            for pk,reasons in legacy.issues.items():issues[pk].update(reasons)
            if row.status=='Planeado':planned.add(row.pk)
            continue
        selected=[s for s in sessions[row.group_id] if s.date<=today and (not start or s.date>=start) and (not end or s.date<=end)]
        if not selected:continue
        unique[row.pk]=row
        if row.status=='Planeado' or row.group.course_id!=row.course_id:
            inconsistent.add(row.pk)
            for bucket in exclusions.values():bucket[row.pk].add('session_identity_or_state')
            continue
        for session in selected:
            item=daily.get(session.pk)
            if not item or not item.confirmed or item.hours is None or session.duration is None:
                for bucket in exclusions.values():bucket[row.pk].add('unverified_session')
                continue
            if item.hours<0 or item.hours>session.duration or item.session.group_id!=row.group_id:
                inconsistent.add(row.pk)
                for bucket in exclusions.values():bucket[row.pk].add('invalid_session_hours')
                continue
            known.add(row.pk)
            hours[row.pk]=hours.get(row.pk,Decimal(0))+item.hours
            old=pairs.get(row.pk,(Decimal(0),Decimal(0)))
            pairs[row.pk]=(old[0]+item.hours,old[1]+session.duration)
            dated[session.date.isoformat()]+=item.hours
            if item.hours>0:
                positive[row.pk]=row.employee_id
                day_pairs.add((row.employee_id,session.date.isoformat()))
    summary=_summarize(unique,scope,{e.pk for e in employees},positive,known,hours,pairs,
                       exclusions,inconsistent,planned,issues)
    if set(positive) & set(exclusions['hours']):
        metrics=dict(summary.metrics)
        metrics['hours_per_person']=replace(metrics['hours_per_person'],value=None,status='to_verify')
        summary=replace(summary,metrics=metrics)
    # Days are complete only when all attendance evidence is dated and verified.
    complete_days=not unallocated and not exclusions['counts']
    return replace(summary,source='SessionAttendance dated hours; undated legacy kept separate',
        effective_days=len(day_pairs) if complete_days and known else None,
        verified_day_pairs=frozenset(day_pairs),dated_hours=dict(dated),
        unallocated_participations=unallocated)

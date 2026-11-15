"""Explicit academic decisions; attendance never decides completion or approval."""
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone

def rounded(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if value is not None else None

def outcomes(rows, reference_date=None, cohort_start=None):
    rows = list({r.pk: r for r in rows}.values())
    reference_date = reference_date or timezone.localdate()
    ended, determined, completed, results, grades, logistics = [], [], [], {}, [], []
    groups = {}
    for row in rows:
        group = row.group if row.group_id else None
        eligible = row.end <= reference_date and (not cohort_start or row.end >= cohort_start) and (group.state == 'Concluída' if group else row.status in ('Concluído', 'Desistência'))
        evaluation = getattr(row, 'evaluation', None)
        valid = bool(evaluation and evaluation.validation == 'Validado' and evaluation.date and evaluation.date <= reference_date)
        if eligible:
            ended.append(row.pk)
            if valid and evaluation.completion is not None:
                determined.append(row.pk)
                if evaluation.completion:
                    completed.append(row.pk)
            if valid and evaluation.validated_grade == row.grade and evaluation.validated_result == row.result:
                if row.result in ('Aprovado', 'Reprovado'):
                    results[row.pk] = row.result
                if row.grade is not None:
                    grades.append(row.grade)
        if valid and evaluation.logistics is not None:
            logistics.append(evaluation.logistics)
        if group:
            groups.setdefault(group.pk, []).append((row, valid and evaluation.logistics is not None))
    weighted = sum(logistics, Decimal(0))
    responses = len(logistics)
    excluded_groups = 0
    for records in groups.values():
        group = records[0][0].group
        aggregate = getattr(group, 'logistics_evaluation', None)
        if not aggregate or aggregate.validation != 'Validado' or not aggregate.date or aggregate.date > reference_date:
            continue
        # An aggregate cannot be split by participant filters or combined with overlapping individual answers.
        full_ids = set(group.trainings.values_list('pk', flat=True))
        if any(valid for _, valid in records) or {r.pk for r, _ in records} != full_ids or not aggregate.responses:
            excluded_groups += 1
            continue
        weighted += aggregate.mean * aggregate.responses
        responses += aggregate.responses
    approved = sum(value == 'Aprovado' for value in results.values())
    mean = weighted / responses if responses else None
    return dict(eligible_ids=set(ended), completed_ids=set(completed), determined_ids=set(determined),
        results=results, ended_count=len(ended), completion_count=len(completed),
        completion_determined=len(determined), completion_pending=len(ended)-len(determined),
        completion_rate=rounded(Decimal(len(completed))*100/len(determined)) if determined else None,
        completion_coverage=rounded(Decimal(len(determined))*100/len(ended)) if ended else None,
        approved=approved, result_count=len(results), result_pending=len(ended)-len(results),
        approval_rate=rounded(Decimal(approved)*100/len(results)) if results else None,
        learning=rounded(sum(grades)/len(grades)) if grades else None, learning_count=len(grades),
        logistics=rounded(mean), logistics_percent=rounded(mean*20) if mean is not None else None,
        logistics_count=responses, logistics_excluded_groups=excluded_groups, reference_date=reference_date)

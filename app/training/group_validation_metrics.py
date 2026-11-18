"""Current, filtered group decisions. Never infer approval from qualitative labels."""
from collections import Counter
from django.core.exceptions import ValidationError
from .models import (Training,ConsolidatedEdition,Course,HistoricalInterfaceLink,
                     TrainingDocumentState,TrainingEvaluation)
from . import group_validation as service

def metrics(rows):
    ids=[r.pk for r in rows]
    final_numeric=0;linked_ids=set()
    for training_id,grades in HistoricalInterfaceLink.objects.filter(training_id__in=ids).values_list('training_id','participation__grades'):
        linked_ids.add(training_id)
        finals=[g for g in grades if isinstance(g,dict) and g.get('stage')=='final']
        if len(finals)==1:
            value=service.number(finals[0].get('value'));scale=service.number(finals[0].get('scale'))
            if value is not None and scale is not None and scale>0 and 0<=value<=scale:final_numeric+=1
    # Standalone operational groups may not yet have a consolidated projection.
    unlinked=set(ids)-linked_ids
    if unlinked:final_numeric+=Training.objects.filter(pk__in=unlinked,grade__gte=0,grade__lte=20).count()
    marked=list(Training.objects.filter(pk__in=ids).exclude(source_data___group_validation_v1__isnull=True).select_related('employee'))
    keys={t.source_data[service.MARKER].get('group') for t in marked if isinstance(t.source_data.get(service.MARKER),dict) and isinstance(t.source_data[service.MARKER].get('group'),str)}
    current={};edition_ids=[];native_keys=[]
    for key in keys:
        try:
            service.keys_checked([key])
        except ValidationError:continue
        if key.startswith('edition:'):edition_ids.append(int(key.split(':')[1]))
        else:native_keys.append(key)
    # Historical groups are the operational universe. Load their dependencies in
    # batches so a dashboard does not execute queries for each validated group.
    if edition_ids:
        editions={e.pk:e for e in ConsolidatedEdition.objects.filter(pk__in=edition_ids,active=True)}
        courses={c.pk:c for c in Course.objects.filter(pk__in=[e.course_id for e in editions.values()])}
        links={l.training_id:l.participation for l in HistoricalInterfaceLink.objects.filter(training_id__in=[t.pk for t in marked]).select_related('participation__edition')}
        docs={d.training_id:d for d in TrainingDocumentState.objects.filter(training_id__in=links)}
        evaluations={e.training_id:e for e in TrainingEvaluation.objects.filter(training_id__in=links)}
        for t in marked:
            marker=t.source_data.get(service.MARKER)
            if not isinstance(marker,dict):continue
            key=marker.get('group','')
            if key not in keys or not key.startswith('edition:'):continue
            try:edition=editions[int(key.split(':')[1])];course=courses[edition.course_id]
            except (KeyError,ValueError):continue
            p=links.get(t.pk)
            if not p or not p.active or p.edition_id!=edition.pk:continue
            current[t.pk]={'fingerprint':service.dependency_fingerprint(t,p,docs.get(t.pk),edition,course,evaluations.get(t.pk))}
    for key in native_keys:
        from django.http import Http404
        from django.core.exceptions import ValidationError
        try:
            for r in service.collect(key)['rows']:current[r['training_id']]=r
        except (Http404,ValidationError):continue
    counts=Counter();review=0
    for t in marked:
        r=current.get(t.pk);marker=t.source_data.get(service.MARKER)
        if not r or not isinstance(marker,dict) or r['fingerprint']!=marker.get('fingerprint'):
            review+=1;continue
        counts[marker['decisions']['academic']]+=1
    approved=counts['Aprovado'];rejected=counts['Reprovado']
    return {'total':len(ids),'numeric_final':final_numeric,'validated':sum(counts.values()),'completed':sum(counts.values()),
            'approved':approved,'rejected':rejected,'pending':counts['Pendente de avaliação'],
            'undefined':counts['Por validar'],'not_applicable':counts['Não aplicável'],'review':review,
            'academic_denominator':approved+rejected,'unvalidated':len(ids)-len(marked)}

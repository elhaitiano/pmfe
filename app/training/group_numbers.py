"""Read-only numbering preview; the scheduling writer reserves under its lock."""
import re
from django.db.models import Max
from .models import TrainingGroup,ConsolidatedEdition,Course

def number_from_label(label):
    """Only explicit numeric labels, never arbitrary digits from names/dates."""
    match=re.fullmatch(r'(?:G(?:rupo)?\s*)?([0-9]+)',str(label or '').strip(),re.I)
    return int(match[1]) if match and int(match[1]) > 0 else None

def documented_legacy_labels(courses):
    """Reserve explicit codes from the preserved index by exact source identity.

    presentation_labels() is deliberately NOT used: its fallback numbers are
    display ordinals, not documentary group codes.
    """
    identities={c.source_action_id:c.pk for c in courses if c.source_system=='epal-proagua-catalogo' and c.source_action_id}
    labels={c.pk:[] for c in courses}
    if identities:
        from .historical import manifest
        for edition in manifest()['editions'].values():
            course_id=identities.get(edition.get('action_id'))
            label=edition.get('documented_group','')
            if course_id is not None and number_from_label(label) is not None:labels[course_id].append(label)
    return labels

def attach(courses):
    courses=list(courses);ids=[c.pk for c in courses]
    counters=dict(Course.objects.filter(pk__in=ids).values_list('pk','next_group'))
    maxima=dict(TrainingGroup.objects.filter(course_id__in=ids).values('course_id').annotate(n=Max('number')).values_list('course_id','n'))
    labels={course_id:[] for course_id in ids}
    for course_id,label in ConsolidatedEdition.objects.filter(course_id__in=ids).values_list('course_id','group_label'):
        labels[course_id].append(label)
        number=number_from_label(label)
        if number is not None:maxima[course_id]=max(maxima.get(course_id) or 0,number)
    legacy=documented_legacy_labels(courses)
    for course_id,values in legacy.items():
        for label in values:maxima[course_id]=max(maxima.get(course_id) or 0,number_from_label(label))
    for c in courses:c.next_group_preview=max(1,counters.get(c.pk,c.next_group) or 1,(maxima.get(c.pk) or 0)+1)
    for c in courses:
        unknown=sum(number_from_label(label) is None for label in labels[c.pk])
        c.group_number_note='Próximo código: maior número identificado + 1, respeitando o contador reservado; não é a quantidade de edições.'
        if unknown:c.group_number_note+=f' {unknown} edição(ões) histórica(s) sem código numérico identificável; códigos não inventados.'
    return courses

def next_number(course):
    return attach([course])[0].next_group_preview

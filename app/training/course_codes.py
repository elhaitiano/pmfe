"""Read-only documentary code presentation; never assigns catalogue fields."""
from collections import defaultdict
from .models import Course,ConsolidatedEdition
from .catalogue_import import digest

def text(value):return str(value or '').strip().upper()

def attach(courses):
    courses=list(courses);ids=[c.pk for c in courses]
    owners=defaultdict(set);source_owners=defaultdict(set)
    for pk,code,approved,source,system,action in Course.objects.values_list('pk','code','internal_code','source_internal_code','source_system','source_action_id'):
        for value in {text(code),text(approved)}:
            if value:owners[value].add(pk)
        if system=='epal-proagua-catalogo' and text(source):source_owners[text(source)].add(action or str(pk))
    documentary=defaultdict(set)
    for pk,reference,evidence in ConsolidatedEdition.objects.filter(course_id__in=ids).values_list('course_id','course_reference','evidence'):
        documented= isinstance(evidence,dict) and any(isinstance(s,dict) and len(str(s.get('document','')))==64 and s.get('preparatory_or_derived_or_synthetic_source') is not True for s in (evidence.get('sources') or []))
        if documented:
            for value in (evidence.get('code'),reference):
                if text(value):documentary[pk].add(text(value))
    for course in courses:
        stored={text(v) for v in (course.code,course.internal_code) if text(v)}
        candidate=text(course.source_internal_code);meta=course.source_metadata if isinstance(course.source_metadata,dict) else {}
        conflict=len(stored)>1 or any(len(owners[c])>1 for c in stored)
        confirmed='';basis='Sem código documental confirmado.'
        if stored and not conflict:
            confirmed=next(iter(stored));basis='Código confirmado já registado no catálogo.'
        elif not stored:
            intact=bool(course.source_system=='epal-proagua-catalogo' and meta and course.source_digest and digest(meta)==course.source_digest and meta.get('id')==course.source_action_id and meta.get('title')==course.title and text(meta.get('code'))==candidate)
            conflict=bool(candidate and (len(source_owners[candidate])>1 or owners[candidate]-{course.pk}))
            if candidate and intact and candidate in documentary[course.pk] and not conflict:
                confirmed=candidate;basis='Código documental conferido: identidade do catálogo e fontes históricas associadas. Não homologa a formação.'
        if conflict:basis='Conflito entre códigos ou identidades de acções. Rever documentalmente; históricos preservados.'
        course.code_profile={'code':confirmed,'label':confirmed or 'Código por confirmar','confirmed':bool(confirmed),'basis':basis,'conflict':conflict,'source_candidate':candidate,'documentary_codes':sorted(documentary[course.pk]),'formal_course_approval':False}
    return courses

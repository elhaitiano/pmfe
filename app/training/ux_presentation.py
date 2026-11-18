"""Read-only projections; no inference of identity, attendance or approval."""
from decimal import Decimal,InvalidOperation
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

def number(value):
    try:
        n=Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None

def grade(training):
    facts=(training.source_data or {}).get('_EPAL_CONSOLIDATED_FACTS',{})
    entries=[g for g in facts.get('grades',[]) if g.get('stage')=='final']
    if entries:
        if len(entries)!=1:return None
        value,scale=number(entries[0].get('value')),number(entries[0].get('scale'))
    elif training.grade is not None:value,scale=number(training.grade),Decimal(20)
    else:
        from .original_assessments import percentage
        value=percentage(training.original_assessment) if training.original_assessment else None
        value,scale=number(value),Decimal(100)
    if value is None or scale is None or scale<=0 or not 0<=value<=scale:return None
    return value,scale

def compact(value):return format(value.normalize(),'f')

def display_rows(rows):
    """Only an exact canonical binding can consolidate two display entries."""
    from .models import HistoricalInterfaceLink,Course
    rows=list(rows)
    links={l.training_id:l for l in HistoricalInterfaceLink.objects.filter(training_id__in=[t.pk for t in rows]).select_related('participation__edition')}
    courses={c.pk:c for c in Course.objects.filter(pk__in={t.course_id for t in rows if t.course_id})}
    seen=set();result=[]
    for t in rows:
        link=links.get(t.pk);key=('participation',link.participation_id) if link else ('training',t.pk)
        if key in seen:continue
        seen.add(key)
        t.ux_title=courses[t.course_id].title if t.course_id in courses else t.title
        t.ux_group=link.participation.edition.group_label if link else ''
        label=str(t.status_label or '—')
        t.ux_status={'Conclusão não documentada':'Por confirmar','Conclusão por confirmar':'Por confirmar','Concluída':'Concluído'}.get(label,label)
        hours=number(t.completed_hours)
        t.ux_hours=hours if hours is not None and hours>=0 else None
        value=grade(t);t.ux_grade_label=compact(value[0])+'/'+compact(value[1]) if value else '—'
        t.ux_grade_percent=float(value[0]*100/value[1]) if value else None
        result.append(t)
    from .operational_status import attach
    attach(result)
    return result

def competencies(rows):
    return [{'training':t,'percent':t.ux_grade_percent} for t in display_rows(rows) if t.ux_grade_percent is not None][:6]

def navigation(request):
    user=getattr(request,'user',None);authenticated=bool(user and user.is_authenticated and user.is_active)
    validate=authenticated and user.has_perms(['training.validate_training_group','training.view_training','training.view_employee'])
    audit=authenticated and user.is_staff and user.has_perm('training.view_auditevent')
    documents=authenticated and user.is_staff and user.has_perm('training.add_privatedocument')
    manage=bool(authenticated and user.is_staff and user.has_perm('training.change_trainingdocumentstate'))
    review=bool(authenticated and user.is_staff and user.has_perms(['training.change_training','training.view_training','training.view_employee']))
    staff_tools=validate or audit or documents or manage or review or bool(authenticated and user.is_superuser)
    match=getattr(request,'resolver_match',None);name=match.url_name if match else '';pk=match.kwargs.get('pk') if match else None
    fallback=reverse('home');section='Início'
    if name in ['employee_data','employee_detail','employee_summary','employee_print'] and pk:
        section='Formandos';fallback=reverse('employee_data',args=[pk]) if name!='employee_data' else reverse('employee_data_select')
    elif request.path.startswith(('/avaliacao/', '/indicadores/', '/competencias/', '/desenvolvimento/', '/minhas-avaliacoes/')):
        section='Avaliação';fallback=reverse('home') if name=='evaluation_index' else reverse('evaluation_index')
    elif name=='participation_executive':section='Consulta executiva';fallback=reverse('dashboard')
    elif 'group' in name or 'course' in name or 'edition' in name or name=='planning':section='Planeamento';fallback=reverse('planning') if name!='planning' else reverse('home')
    elif 'document' in name or 'certificate' in name:section='Certificados'
    elif 'report' in name:section='Relatórios'
    crumbs=[{'label':'Início','url':reverse('home')}]
    if section=='Formandos':
        crumbs.append({'label':'Formandos','url':reverse('employee_data_select')})
        if pk and name=='employee_detail':crumbs.append({'label':'Ficha individual','url':reverse('employee_data',args=[pk])})
        crumbs.append({'label':'Histórico' if name=='employee_detail' else 'Ficha individual','url':''})
    elif section=='Planeamento':
        crumbs.append({'label':'Planeamento','url':reverse('planning') if name!='planning' else ''})
        if name=='course_groups' and pk:crumbs.append({'label':'Formação e grupos','url':''})
        elif name in ['consolidated_edition','group_detail'] and pk:
            from .models import ConsolidatedEdition,TrainingGroup
            model=ConsolidatedEdition if name=='consolidated_edition' else TrainingGroup
            course_id=model.objects.filter(pk=pk).values_list('course_id',flat=True).first()
            if course_id:
                fallback=reverse('course_groups',args=[course_id]);crumbs.append({'label':'Formação','url':fallback})
            crumbs.append({'label':'Grupo e participantes','url':''})
    elif section=='Avaliação':
        crumbs=[{'label':'Gestão de Formações','url':reverse('home')}]
        crumbs.append({'label':'Avaliação','url':'' if name=='evaluation_index' else reverse('evaluation_index')})
        if name=='indicator_index':crumbs.append({'label':'Indicadores confirmados e 360°','url':''})
        elif name=='competency_index':crumbs.append({'label':'Ciclos e competências','url':''})
        elif name=='development_index':crumbs.append({'label':'Desenvolvimento','url':''})
    elif section!='Início':crumbs.append({'label':section,'url':''})
    candidate=request.GET.get('return_to') or request.META.get('HTTP_REFERER','')
    from urllib.parse import urlsplit
    back=fallback
    if candidate and url_has_allowed_host_and_scheme(candidate,{request.get_host()},require_https=request.is_secure()):
        parsed=urlsplit(candidate)
        if parsed.path.startswith('/') and not parsed.path.startswith('//') and chr(92) not in parsed.path and parsed.path!=request.path and parsed.path not in ['/entrar/','/sair/']:
            back=parsed.path+('?' + parsed.query if parsed.query else '')
    return {'ux_admin_visible':staff_tools,'ux_can_validate':validate,'ux_can_audit':audit,'ux_can_upload':documents,'ux_back_url':back,'ux_section':section,'ux_crumbs':crumbs}

from django.views.decorators.http import require_GET

@login_required
@require_GET
def administration(request):
    if not navigation(request)['ux_admin_visible']:raise PermissionDenied
    return render(request,'ux_administration.html')

def certificate_cards(rows,user):
    cards=[];seen=set()
    from .documents import authorised
    allowed=user.is_active and user.is_staff and user.has_perm('training.view_documentaryrecord')
    for t in rows:
        for cert in t.validated_certificates:
            key=cert.get('key')
            if not key or key in seen:continue
            seen.add(key);urls=[]
            if allowed:
                for doc in cert.get('documents',[]):
                    if doc.get('sha256'):
                        urls.append(reverse('certificate_source_pdf',args=[t.pk,doc['sha256']])+'?view=1')
            cards.append({'title':getattr(t,'ux_title',t.title),'number':cert.get('number') or '—','url':urls[0] if urls else None,'delivered':cert.get('delivered') is True})
    return cards

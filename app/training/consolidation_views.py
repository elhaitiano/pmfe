from .professional_ui import person_ids
from .training_areas import AREAS
"""Shared source-backed historical screens and exports."""
import hashlib,io,json,mimetypes
from collections import Counter
from functools import wraps
from pathlib import Path
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count,Q
from django.http import FileResponse,Http404,HttpResponse
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.views.decorators.http import require_GET
from . import consolidation as service
from .models import ConsolidatedEdition,ConsolidatedParticipation,ConsolidationRun,Employee,HistoricalCertificatePDF
from .certificate_evidence import io_path


def access(view):
    @login_required
    @require_GET
    @wraps(view)
    def guarded(request,*args,**kwargs):
        if not service.enabled():raise Http404
        if not request.user.has_perms(['training.view_employee','training.view_training']):raise PermissionDenied
        return view(request,*args,**kwargs)
    return guarded


def context(request,rows=None):
    rows=service.filtered(service.summary_participations() if rows is None else rows,request.GET)
    query=request.GET.copy();query.pop('page',None)
    choices={}
    for e in ConsolidatedEdition.objects.filter(active=True).select_related('course').defer('evidence','course__source_metadata'):
        choices.setdefault(e.course_reference,{'course_reference':e.course_reference,'title':e.course.title if e.course_id else e.title})
    chart_people={};chart_titles={}
    for p in rows:
        chart_people.setdefault(p.edition.course_reference,set()).add(p.employee_id);chart_titles[p.edition.course_reference]=p.edition.title
    peak=max((len(v) for v in chart_people.values()),default=1)
    chart=[{'title':chart_titles[k],'count':len(v),'width':len(v)*100/peak} for k,v in sorted(chart_people.items(),key=lambda kv:len(kv[1]),reverse=True)[:10]]
    from .operational_status import from_participations,summary
    operational_summary=summary(from_participations(rows))
    return {'operational_summary':operational_summary,'consolidated_mode':True,'consolidated_stats':service.stats(rows),'page':Paginator(rows.order_by('-edition__start','employee__name').defer(None),35).get_page(request.GET.get('page')),
        'formation_chart':chart,'dashboard_mode':request.path.startswith('/dashboard'),
        'filters':request.GET,'courses':sorted(choices.values(),key=lambda c:c['title']),
        'editions':ConsolidatedEdition.objects.filter(active=True).defer('evidence').order_by('course_reference','-start','group_label'),
        'directions':service.workers().exclude(direction='').values_list('direction',flat=True).distinct().order_by('direction'),
        'years':ConsolidatedEdition.objects.filter(active=True).dates('start','year',order='DESC'),
        'query_without_page':query.urlencode()}


@access
def history(request):
    data=context(request)
    if request.path.rstrip('/')=='/dashboard':
        from .original_presentation import dashboard
        data.update(dashboard(service.filtered(service.participations(),request.GET)))
        return render(request,'dashboard.html',data)
    return render(request,'consolidated/history.html',data)


@access
def home(request):
    data={'consolidated_mode':True,'directions':service.workers().exclude(direction='').values_list('direction',flat=True).distinct().order_by('direction')}
    workers=service.workers().annotate(training_count=Count('consolidated_participations',filter=Q(consolidated_participations__active=True),distinct=True)).order_by('name')
    query=request.GET.get('q','').strip()
    if query:workers=workers.filter(Q(pk__in=person_ids(query)))
    direction=request.GET.get('direction','');status=request.GET.get('status','')
    if direction:workers=workers.filter(direction=direction)
    if status=='trained':workers=workers.filter(training_count__gt=0)
    if status=='untrained':workers=workers.filter(training_count=0)
    total=service.workers().count();trained=service.participations().values('employee_id').distinct().count()
    data.update(worker_page=Paginator(workers,35).get_page(request.GET.get('page')),official_count=total,with_history=trained,without_history=total-trained,
        reconciliation=ConsolidationRun.objects.filter(active=True).first().report)
    selected=request.GET.get('employee','');person=service.workers().filter(pk=int(selected)).first() if selected.isdecimal() and len(selected)<18 else None
    from .views import employee_fields
    from . import original_presentation
    data.update(page=Paginator(workers,30).get_page(request.GET.get('page')),q=query,direction=direction,status=status,
        employee_count=total,trained_count=trained,untrained_count=total-trained,coverage=trained*100/total if total else 0,
        employee=person,profile_fields=employee_fields(person) if person else [],selected_stats=original_presentation.profile(service.participations().filter(employee=person))['stats'] if person else {})
    return render(request,'home.html',data)


@access
def employee(request,pk,printing=False):
    person=get_object_or_404(Employee,pk=pk)
    from . import data_validation
    data_validation.require_profile_access(request.user,person)
    rows=service.participations().filter(employee=person)
    data=context(request,rows);data.update(employee=person,printing=printing)
    if printing:data['page']=Paginator(service.filtered(rows,request.GET).order_by('-edition__start'),max(rows.count(),1)).get_page(1)
    groups={}
    for p in service.filtered(rows,request.GET).order_by('edition__start'):
        for grade in p.grades:
            if grade['stage']=='final':groups.setdefault((grade['scale'],grade.get('phase','')),[]).append({'value':grade['value'],'title':p.edition.title,'date':str(p.edition.start)})
    from decimal import Decimal
    charts=[]
    for (scale,phase),points in groups.items():
        for i,point in enumerate(points):point.update(x=round(30+i*700/max(len(points)-1,1),1),y=round(180-float(Decimal(point['value'])/Decimal(scale))*150,1))
        charts.append({'scale':scale,'phase':phase,'points':points,'coordinates':' '.join(str(p['x'])+','+str(p['y']) for p in points)})
    data['evolution_charts']=charts
    if printing:return render(request,'consolidated/employee.html',data)
    from . import original_presentation
    from .analytics import profile_fields
    data.update(original_presentation.profile(service.filtered(rows,request.GET).order_by('-edition__start')))
    data.update(data_fields=[(label,value) for label,value in profile_fields(person) if label!='Centro de Custo'],year=request.GET.get('year',''),area=request.GET.get('area',''),
        years=sorted({p.edition.start.year for p in rows if p.edition.start},reverse=True),areas=list(AREAS))
    if request.GET.get('area'):data['trainings']=[t for t in data['trainings'] if t.area==request.GET['area']]
    if '/dados/' in request.path:
        from .models import Training
        from .document_views import profile_context
        from .historical_exception_views import profile as historical_profile
        data.update(profile_context(request.user,person,list(Training.objects.filter(employee=person))))
        if request.user.is_staff and request.user.has_perm('training.view_documentaryrecord'):data.update(historical_profile(person))
        data['trainings']=data['trainings'][:5]
        return render(request,'employee_data.html',data)
    return render(request,'employee.html',data)


@access
def edition(request,pk):
    from .interface_groups import detail
    return detail(request,pk)
    item=get_object_or_404(ConsolidatedEdition,pk=pk,active=True)
    data=context(request,service.participations().filter(edition=item));data['edition']=item
    params=request.GET.copy();params['group']=str(item.pk);params.pop('page',None)
    data.update(filters=params,query_without_page=params.urlencode())
    return render(request,'consolidated/history.html',data)


@access
def participation(request,pk):
    p=get_object_or_404(service.participations(),pk=pk)
    from .models import HistoricalInterfaceLink
    link=HistoricalInterfaceLink.objects.select_related('training').filter(participation=p).first()
    interface_training=link.training if link else None
    ids=list(set(p.legacy_training_ids)|({interface_training.pk} if interface_training else set()));doc_ids=p.documentary_participation_ids
    from .historical_review import active_pdfs
    pdfs=active_pdfs().filter(Q(training_id__in=ids)|Q(documentary_participation_id__in=doc_ids))
    from .models import TrainingDocumentState,HistoricalDocumentaryParticipation
    deliveries=list(TrainingDocumentState.objects.filter(training_id__in=ids))+list(HistoricalDocumentaryParticipation.objects.filter(pk__in=doc_ids))
    current_state=TrainingDocumentState.objects.filter(training=interface_training).first() if interface_training else None
    if current_state:deliveries=[current_state]
    return render(request,'consolidated/participation.html',{'consolidated_mode':True,'participation':p,'edition':p.edition,
        'sources':p.evidence.get('sources',[]),'raw':json.dumps({'participation':p.evidence,'edition':p.edition.evidence},ensure_ascii=False,indent=2),'pdfs':pdfs,'deliveries':deliveries,'interface_training':interface_training})


@access
def source(request,sha256):
    if not request.user.is_staff:raise PermissionDenied
    run=ConsolidationRun.objects.get(active=True);relative=run.report.get('source_files',{}).get(sha256)
    if not relative:raise Http404
    if relative=='SNAPSHOT_MENSAL_ATUAL/snapshot-mensal-atual.xlsx':
        path=Path(run.report['plan_directory']).parent/'conferencia-integral-20261104/snapshot-mensal-atual.xlsx'
    else:
        root=Path(getattr(settings,'PMF_CERTIFICATE_SOURCE_ROOT','')).resolve();path=(root/relative).resolve()
        if not path.is_relative_to(root):raise PermissionDenied
    try:content=io_path(path).read_bytes()
    except OSError:raise Http404('Documento de origem indisponível.')
    if hashlib.sha256(content).hexdigest()!=sha256:return HttpResponse('Fonte alterada; é necessária nova conferência.',status=409)
    response=FileResponse(io.BytesIO(content),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
        as_attachment=path.suffix.lower()!='.pdf' or request.GET.get('view')!='1',filename=path.name)
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    if path.suffix.lower()=='.pdf':response['Content-Security-Policy']="sandbox; default-src 'none'"
    return response


@access
def reconciliation(request):
    report=ConsolidationRun.objects.get(active=True).report
    rows=service.participations();problem=rows.exclude(evidence__issues=[])
    return render(request,'consolidated/reconciliation.html',{'consolidated_mode':True,'report':report,'consolidated_stats':service.stats(rows),
        'problem_page':Paginator(problem.order_by('employee__name'),35).get_page(request.GET.get('page'))})


@access
def reconciliation_export(request):
    if not request.user.is_staff:raise PermissionDenied
    run=ConsolidationRun.objects.get(active=True)
    content=(Path(run.report['plan_directory'])/'CONFERENCIA_E_DECISOES.xlsx').read_bytes()
    response=FileResponse(io.BytesIO(content),as_attachment=True,filename='EPAL-conferencia-e-decisoes.xlsx',content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Cache-Control']='private, no-store';return response


def export_bytes(rows):
    from openpyxl import Workbook
    from .models import HistoricalInterfaceLink
    from .certificate_register import row_summary
    from decimal import Decimal
    rows=list(rows)
    interface={link.participation_id:link.training for link in HistoricalInterfaceLink.objects.filter(participation_id__in=[p.pk for p in rows]).select_related('training')}
    w=Workbook();s=w.active;s.title='Participações'
    s.append(['Mecanográfico','Formando','Formação','Grupo','Edição','Início','Fim','Local','Formador','Duração dias','Horas previstas','Presenças','Ausências','Assiduidade %','Horas frequentadas','Notas e escalas','Resultado registado','Históricos anteriores','Referências documentais','Certificados entregues (sem repetição)','Horas certificadas (sem repetição)','Estado de entrega','Data de entrega','Data de emissão','PDFs de origem'])
    for p in rows:
        e=p.edition;t=interface.get(p.pk);certificate=row_summary(t) if t else {};hours=certificate.get('hours')
        s.append([p.employee.number,p.employee.name,e.title,e.group_label,e.source_key,str(e.start or ''),str(e.end or ''),e.location,e.trainer,
            e.declared_days,e.total_hours,p.present_count,p.absent_count,p.attendance_percent,p.attended_hours,
            '; '.join(g['stage']+': '+g['value']+'/'+g['scale'] for g in p.grades),p.result,json.dumps(p.legacy_training_ids),json.dumps(p.evidence.get('sources',[]),ensure_ascii=False),
            certificate.get('delivered_count',int(t.delivery_reported)) if t else None,Decimal(hours) if hours is not None else None,
            'Entregue' if t and t.delivery_reported else 'Não entregue' if t and t.certified is False else 'Por confirmar',str(t.certificate_delivery_date or '') if t else '',str(t.certificate_issue_date or '') if t else '',
            '\n'.join(path for cert in (t.validated_certificates if t else []) for document in cert['documents'] for path in document['paths'])])
    s.freeze_panes='A2';s.auto_filter.ref=s.dimensions
    for row in s:
        for c in row:
            if isinstance(c.value,str):c.data_type='s'
    for col in s.columns:s.column_dimensions[col[0].column_letter].width=24
    roster=w.create_sheet('Cadastro oficial');roster.append(['Mecanográfico','Formando','Direção','Categoria','Função','Telefone','Área'])
    for p in service.workers().order_by('name'):roster.append([p.number,p.name,p.direction,p.category,p.function,p.phone,p.area])
    for row in roster:
        for c in row:
            if isinstance(c.value,str):c.data_type='s'
    roster.freeze_panes='A2';roster.auto_filter.ref=roster.dimensions
    from .operational_status import from_participations,summary
    ops=from_participations(rows);totals=summary(ops)
    extra=w.create_sheet('Conclusões e certificados');extra.append(['Indicador','Valor'])
    from .operational_status import LABELS
    for key,value in totals.items():extra.append([LABELS.get(key,key),value])
    details=w.create_sheet('Estados por participação');details.append(['TrainingID','Conclusão comprovada','Certificado','Motivos','Revisão documental'])
    for r in ops:details.append([r.get('training_id'),r['completion'],r['certificate'],'; '.join(r['reasons']),bool(r['review'])])
    data=io.BytesIO();w.save(data);return data.getvalue()


@access
def export(request):
    rows=service.filtered(service.participations(),request.GET)
    response=FileResponse(io.BytesIO(export_bytes(rows)),as_attachment=True,filename='EPAL-historicos-consolidados.xlsx',content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Cache-Control']='private, no-store';return response


def pdf_bytes(rows,title='EPAL — Histórico consolidado'):
    from reportlab.platypus import SimpleDocTemplate,Spacer
    from reportlab.lib.pagesizes import landscape,A4
    from .executive_pdf import para,table
    rows=list(rows);counts=service.stats(rows);out=io.BytesIO()
    story=[para(title,'ExecTitle'),para(f"{counts['people']} formandos distintos · {counts['participations']} participações/inscrições · {counts['actions']} formações · {counts['editions']} edições"),
        para('Escalas originais preservadas. Menções calculadas apenas pelas fórmulas da fonte. Horas previstas e horas frequentadas são distintas.'),Spacer(1,10)]
    from .operational_status import from_participations,summary
    ops=summary(from_participations(rows))
    story.append(para('Conclusão comprovada: '+str(ops['completed_confirmed'])+' · Certificados previstos: '+str(ops['expected'])+' · Emitidos: '+str(ops['issued'])+' · Entregues: '+str(ops['delivered'])))
    values=[]
    for p in rows:
        e=p.edition
        values.append([p.employee.number+' — '+p.employee.name,e.title,('G'+e.group_label if e.group_label else 'Não indicado')+' / H-'+str(e.pk),
            str(e.start)+' / '+str(e.end),str(e.total_hours) if e.total_hours is not None else 'Sem prova única',
            str(p.attended_hours) if p.attended_hours is not None else 'Sem prova suficiente',
            str(p.attendance_percent)+'%' if p.attendance_percent is not None else 'Sem denominador completo',
            '; '.join(g['stage']+': '+g['value']+'/'+g['scale'] for g in p.grades),p.result or 'Não declarado'])
    widths=[105,155,55,70,50,55,65,115,95]
    available=landscape(A4)[0]-36
    widths=[w*available/sum(widths) for w in widths]
    story.append(table(['Formando','Formação','Grupo/edição','Período','Horas totais','Horas frequentadas','Assiduidade','Notas / escalas','Resultado da fonte'],values,widths,long=True))
    SimpleDocTemplate(out,pagesize=landscape(A4),leftMargin=18,rightMargin=18,topMargin=24,bottomMargin=24).build(story)
    return out.getvalue()


@access
def pdf(request):
    rows=service.filtered(service.summary_participations(),request.GET)
    response=FileResponse(io.BytesIO(pdf_bytes(rows)),content_type='application/pdf',as_attachment=True,filename='EPAL-historico-consolidado.pdf')
    response['Cache-Control']='private, no-store';return response

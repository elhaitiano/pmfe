import hashlib, io, os, uuid
from pathlib import Path
from types import SimpleNamespace
from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.utils.text import slugify
from .analytics import statistics
from .executive import selection, analyse, slice_rows
from .models import ExecutiveReport
from .kpi_reporting import archive_summary, DEFINITIONS
from .kpi_catalogue import catalogue
from .kpi_service import Scope
from .views import staff_required, record


def context(request):
    form, selected, start, end, label, rows, employees, base, valid=selection(request)
    data=analyse(rows,employees,selected,start,end,label,base)
    data.update(filter_form=form,export_query=data['query'])
    return data,valid


@login_required
def explore(request):
    data,valid=context(request)
    if not (request.user.is_staff or request.user.has_perms(['training.view_employee','training.view_training'])):
        return HttpResponse('Sem permissão para consulta nominal.',status=403)
    kind=request.GET.get('slice','all');value=request.GET.get('value','')
    if kind not in ('all','area','direction','status','result','time','person','certificate','deliveries','endings'):raise Http404
    rows=data['deliveries'] if kind=='deliveries' else data['ended'] if kind=='endings' else slice_rows(data['rows'],kind,value,data['selected'].get('time_basis'))
    reference = data['employees']
    if kind == 'person':
        reference = [e for e in reference if str(e.pk) == value]
    elif kind == 'direction':
        reference = [e for e in reference if (e.direction in ('', 'Direcção por confirmar') if value == '__unconfirmed__' else e.direction == value)]
    from dataclasses import replace
    from datetime import date
    import calendar
    detail_scope = replace(data['kpi_summary'].scope, filters={**data['selected'], 'reference':None, 'slice':kind, 'value':value},reference='current_registry')
    if kind=='time' and detail_scope.time_basis=='session_date':
        try:
            parts=[int(v) for v in value.split('-')]
            year=parts[0];month=parts[1] if len(parts)>1 else 1
            first=date(year,month,parts[2] if len(parts)>2 else 1)
            last=first if len(parts)>2 else date(year,month,calendar.monthrange(year,month)[1]) if len(parts)>1 else date(year,12,31)
            detail_scope=replace(detail_scope,date_from=first,date_to=last)
        except (ValueError, IndexError):
            raise Http404
    if kind in ('deliveries','endings'):
        detail_scope = Scope({**data['selected'], 'slice':kind, 'value':value},
        data['period_label'], 'all_records' if kind in ('deliveries', 'endings') else 'training_start', 'current_registry')
    data.update(detail_title=request.GET.get('label','')[:200] or 'Participações seleccionadas',detail_stats=statistics(rows, reference, detail_scope),detail_count=len(rows),page=Paginator(rows,25).get_page(request.GET.get('page')))
    query=request.GET.copy();query.pop('page',None);query.pop('fragment',None)
    data['detail_query']=query.urlencode()
    return render(request,'executive_detail_fragment.html' if request.GET.get('fragment')=='1' else 'executive_explore.html',data,status=200 if valid else 400)


@login_required
def reports(request):
    data,valid=context(request)
    q=request.GET.get('q','').strip()[:200]
    archive=ExecutiveReport.objects.all()
    if q:archive=archive.filter(title__icontains=q)
    from .workforce import evidence_fingerprint
    fingerprint=evidence_fingerprint()
    archive_page=Paginator(archive,15).get_page(request.GET.get('page'))
    for item in archive_page:
        # Pre-policy archives remain available only through the nominal permission check.
        item.protected=item.summary.get('privacy_restricted',True)
        item.needs_review=item.summary.get('source_fingerprint')!=fingerprint
    data.update(archive_page=archive_page,archive_search=q)
    from .models import WorkforceReference
    data['drh_references']=WorkforceReference.objects.filter(approved=True)
    if data.get('privacy_restricted'):
        data['kpi_details']=[];data['stats']={};data['insights']=['Agregação protegida: <5 pessoas.']

    return render(request,'executive_reports.html',data)


@login_required
def preview_pdf(request):
    data,valid=context(request)
    if not data.get('privacy_restricted') and not (request.user.is_staff or request.user.has_perms(['training.view_employee','training.view_training'])):
        return HttpResponse('Sem permissão para o conteúdo nominal do PDF.',status=403)
    if not valid:return HttpResponseBadRequest('Seleccione um período válido antes de gerar o PDF.')
    from .executive_pdf import render_report
    payload=render_report(data,actor=request.user.username)
    return FileResponse(io.BytesIO(payload),content_type='application/pdf',as_attachment=True,filename='EPAL_Relatorio_Executivo.pdf')


@login_required
@staff_required
@require_POST
def create_report(request):
    # Use the submitted, validated filters, never a stale browser summary.
    proxy=SimpleNamespace(GET=request.POST)
    with transaction.atomic():
        data,valid=context(proxy)
        if not valid:return HttpResponseBadRequest('Filtros inválidos. Reabra o Centro de Relatórios e reveja a selecção.')
        predecessor=None
        if request.POST.get('predecessor'):
            try: predecessor=ExecutiveReport.objects.select_for_update().get(pk=request.POST['predecessor'])
            except (ExecutiveReport.DoesNotExist,ValidationError):return HttpResponseBadRequest('Versão anterior inválida.')
            if ExecutiveReport.objects.filter(predecessor=predecessor).exists():return HttpResponse('Já existe uma rectificação; utilize a versão mais recente.',status=409)
            if not request.POST.get('correction_reason','').strip():return HttpResponseBadRequest('Indique o motivo da rectificação.')
        from .executive_pdf import render_report
        report_id=uuid.uuid4()
        pdf=render_report(data,str(report_id),request.user.username)
        destination=Path(settings.DATA_DIR)/'reports';destination.mkdir(parents=True,exist_ok=True)
        path=destination/f'{report_id}.pdf';temporary=destination/f'{report_id}.tmp'
        filters={k:v.isoformat() if hasattr(v,'isoformat') else getattr(v, 'pk', v) for k,v in data['selected'].items()}
        if predecessor and {k:v for k,v in filters.items() if k!='reference'}!={k:v for k,v in predecessor.filters.items() if k!='reference'}:
            return HttpResponseBadRequest('A rectificação deve conservar os mesmos filtros e período. Gere uma série independente para outro âmbito.')
        from .workforce import evidence_fingerprint
        fingerprint=evidence_fingerprint()
        try:
            temporary.write_bytes(pdf);os.replace(temporary,path)
            ExecutiveReport.objects.create(id=report_id,predecessor=predecessor,revision=predecessor.revision+1 if predecessor else 1,correction_reason=request.POST.get('correction_reason','').strip() if predecessor else '',definitions_version='kpi-10-v1',actor=request.user.username,title=(data['period_label']+' - '+(filters.get('direction') or 'Geral'))[:250],filters=filters,
                summary={'indicator_catalogue':archive_summary(catalogue(data['stats'])),'definitions':DEFINITIONS,'participations':len(data['rows']),'hours':str(data['stats']['hours']),'certificates':data['stats']['certificates'],'dated_deliveries':len(data['deliveries']),'period':data['period_label'],'kpi':archive_summary(data['kpi_summary']), 'outcomes':archive_summary(data['stats']['outcomes']), 'certification':archive_summary(data['stats']['certification']), 'privacy_restricted':data.get('privacy_restricted',False), 'reference':str(data['kpi_summary'].scope.reference), 'source_fingerprint':fingerprint},sha256=hashlib.sha256(pdf).hexdigest())
            record(request,'EXEC_REPORT',f'{report_id} - {data["period_label"]}')
        except IntegrityError:
            path.unlink(missing_ok=True);temporary.unlink(missing_ok=True)
            return HttpResponse('A versão foi rectificada noutra janela. Reabra o arquivo.',status=409)
        except Exception:
            path.unlink(missing_ok=True);temporary.unlink(missing_ok=True);raise
    return FileResponse(io.BytesIO(pdf),content_type='application/pdf',as_attachment=True,filename=f'EPAL_Relatorio_{slugify(data["period_label"] + "-" + (filters.get("direction") or "Geral"))[:80]}_{str(report_id)[:8]}.pdf')


@login_required
def download_report(request,pk):
    item=get_object_or_404(ExecutiveReport,pk=pk)
    if not item.summary.get('privacy_restricted',False) and not (request.user.is_staff or request.user.has_perms(['training.view_employee','training.view_training'])):return HttpResponse('Sem permissão para consultar o conteúdo nominal deste arquivo.',status=403)
    path=Path(settings.DATA_DIR)/'reports'/f'{item.pk}.pdf'
    if not path.is_file():raise Http404('O ficheiro arquivado não está disponível.')
    payload=path.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=item.sha256:return HttpResponse('O PDF não corresponde ao arquivo original. Verifique a cópia de segurança.',status=409)
    return FileResponse(io.BytesIO(payload),content_type='application/pdf',as_attachment=True,filename=f'EPAL_Relatorio_{slugify(item.title)[:80]}_{str(item.pk)[:8]}.pdf')

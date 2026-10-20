import hashlib, io, os, uuid
from pathlib import Path
from types import SimpleNamespace
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.utils.text import slugify
from .analytics import statistics
from .executive import selection, analyse, slice_rows
from .models import ExecutiveReport
from .views import staff_required, record


def context(request):
    form, selected, start, end, label, rows, employees, base, valid=selection(request)
    data=analyse(rows,employees,selected,start,end,label,base)
    data.update(filter_form=form,export_query=data['query'])
    return data,valid


@login_required
def explore(request):
    data,valid=context(request)
    kind=request.GET.get('slice','all');value=request.GET.get('value','')
    if kind not in ('all','area','direction','status','result','time','person','certificate','deliveries','endings'):raise Http404
    rows=data['deliveries'] if kind=='deliveries' else data['ended'] if kind=='endings' else slice_rows(data['rows'],kind,value)
    data.update(detail_title=request.GET.get('label','')[:200] or 'Participações seleccionadas',detail_stats=statistics(rows),detail_count=len(rows),page=Paginator(rows,25).get_page(request.GET.get('page')))
    query=request.GET.copy();query.pop('page',None);query.pop('fragment',None)
    data['detail_query']=query.urlencode()
    return render(request,'executive_detail_fragment.html' if request.GET.get('fragment')=='1' else 'executive_explore.html',data,status=200 if valid else 400)


@login_required
def reports(request):
    data,valid=context(request)
    q=request.GET.get('q','').strip()[:200]
    archive=ExecutiveReport.objects.all()
    if q:archive=archive.filter(title__icontains=q)
    data.update(archive_page=Paginator(archive,15).get_page(request.GET.get('page')),archive_search=q)
    return render(request,'executive_reports.html',data)


@login_required
def preview_pdf(request):
    data,valid=context(request)
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
        from .executive_pdf import render_report
        report_id=uuid.uuid4()
        pdf=render_report(data,str(report_id),request.user.username)
        destination=Path(settings.DATA_DIR)/'reports';destination.mkdir(parents=True,exist_ok=True)
        path=destination/f'{report_id}.pdf';temporary=destination/f'{report_id}.tmp'
        filters={k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in data['selected'].items()}
        try:
            temporary.write_bytes(pdf);os.replace(temporary,path)
            ExecutiveReport.objects.create(id=report_id,actor=request.user.username,title=(data['period_label']+' - '+(filters.get('direction') or 'Geral'))[:250],filters=filters,
                summary={'participations':len(data['rows']),'hours':str(data['stats']['hours']),'certificates':data['stats']['certificates'],'dated_deliveries':len(data['deliveries']),'period':data['period_label']},sha256=hashlib.sha256(pdf).hexdigest())
            record(request,'EXEC_REPORT',f'{report_id} - {data["period_label"]}')
        except Exception:
            path.unlink(missing_ok=True);temporary.unlink(missing_ok=True);raise
    return FileResponse(io.BytesIO(pdf),content_type='application/pdf',as_attachment=True,filename=f'EPAL_Relatorio_{slugify(data["period_label"] + "-" + (filters.get("direction") or "Geral"))[:80]}_{str(report_id)[:8]}.pdf')


@login_required
def download_report(request,pk):
    item=get_object_or_404(ExecutiveReport,pk=pk)
    path=Path(settings.DATA_DIR)/'reports'/f'{item.pk}.pdf'
    if not path.is_file():raise Http404('O ficheiro arquivado não está disponível.')
    payload=path.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=item.sha256:return HttpResponse('O PDF não corresponde ao arquivo original. Verifique a cópia de segurança.',status=409)
    return FileResponse(io.BytesIO(payload),content_type='application/pdf',as_attachment=True,filename=f'EPAL_Relatorio_{slugify(item.title)[:80]}_{str(item.pk)[:8]}.pdf')

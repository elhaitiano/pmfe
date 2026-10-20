"""Authenticated, strictly GET historical reports; no persistence or enrolments."""
import io
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404,FileResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from . import historical as service

def access(view):
    @login_required
    @require_GET
    @never_cache
    @wraps(view)
    def guarded(request,*args,**kwargs):
        if not service.enabled():raise Http404
        if not request.user.has_perms(['training.view_training','training.view_employee']):raise PermissionDenied
        return view(request,*args,**kwargs)
    return guarded

def context(request,edition_id=None):
    allrows=service.records();params=request.GET.copy()
    if edition_id:
        if edition_id not in service.manifest()['editions']:raise Http404
        params['edition']=edition_id
    rows=service.filtered(allrows,params)
    return {'historical_rows':rows,'historical_stats':service.metrics(rows),'historical_editions':service.editions(rows),'age_bands':service.AGE_BANDS,'years':sorted({r['training'].start.year for r in allrows},reverse=True),'directions':sorted({r['training'].employee.direction for r in allrows if r['training'].employee.direction}),'actions':sorted({(r['course'].source_action_id,r['course'].title) for r in allrows if r['course']}),'edition_choices':service.editions(allrows),'filters':params,'filter_query':params.urlencode(),'is_detail':bool(edition_id),'edition_id':edition_id,'show_nominal':bool(edition_id)}

@access
def index(request):return render(request,'planning/historical.html',context(request))

@access
def detail(request,edition_id):return render(request,'planning/historical.html',context(request,edition_id))

def make_pdf(data):
    from reportlab.platypus import SimpleDocTemplate,Spacer
    from .executive_pdf import para,table,BLUE
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    output=io.BytesIO();doc=SimpleDocTemplate(output,pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,topMargin=30*mm,bottomMargin=20*mm)
    s=data['historical_stats'];story=[para('EPAL / PROÁGUA — Grupos históricos','ExecTitle'),para('Idade na data de início da participação. Correspondências prováveis não são grupos confirmados.'),para('Filtros: '+data['filter_query']),table(['Participações','Trabalhadores','Grupos confirmados'],[[s['participations'],s['people'],s['groups']]]),Spacer(1,12),table(['Faixa etária','Participações'],[[r['label'],r['count']] for r in s['ages']]),Spacer(1,12),table(['Edição','Período','Participações','Evidência'],[[g['edition']['display_id'],g['edition']['start']+' / '+g['edition']['end'],g['stats']['participations'],'Confirmada parcialmente' if g['stats']['confirmed'] else 'Provável'] for g in data['historical_editions']])]
    display=lambda v:'Por confirmar' if v is None else str(v)
    attendance_label=f"{s['attendance']:.1f}%" if s['attendance'] is not None else 'Por confirmar'
    story.insert(4,para(f"Assiduidade documentada: {attendance_label}; cobertura {s['attendance_coverage']}/{s['participations']}. Emissão declarada: {display(s['issued_value'])}; entrega ao formando: {display(s['learner_delivery_value'])}; entrega aos RH: {display(s['hr_delivery_value'])}. Aproveitamentos percentuais na fonte: {s['achievement_percent_count']}; equivalências /20 calculadas apenas quando o aproveitamento final está comprovado; originais preservados."))
    def header(canvas,doc):
        canvas.setFillColor(BLUE);canvas.setFont('Helvetica',9);canvas.drawString(18*mm,A4[1]-18*mm,'EPAL — Empresa Pública de Água | PROÁGUA');canvas.drawRightString(A4[0]-18*mm,12*mm,str(doc.page))
    doc.build(story,onFirstPage=header,onLaterPages=header)
    return output.getvalue()

@access
def pdf(request):
    response=FileResponse(io.BytesIO(make_pdf(context(request))),content_type='application/pdf',as_attachment=True,filename='EPAL_Grupos_Historicos.pdf')
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response

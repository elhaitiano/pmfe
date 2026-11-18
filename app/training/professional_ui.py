"""Read-only navigation services for the isolated application; no new data models."""
from pathlib import Path
from datetime import timedelta
from time import perf_counter
import io,json,unicodedata,hashlib
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse,Http404,FileResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from .models import Employee,TrainingGroup,Training,Enrollment

def scope():
    if not getattr(settings,'PMF_CLEAN_BASE_MODE',False):raise Http404
def normalise(value):return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',str(value).casefold()) if not unicodedata.combining(c)).split())
def directory():
    key='pmfe-person-directory:'+hashlib.sha256(str(settings.DATABASES['default']['NAME']).encode()).hexdigest();items=cache.get(key)
    if items is None:
        items=[{'id':p['id'],'name':p['name'],'number':p['number'],'direction':p['direction'],'normal':normalise(p['name']),'numeric':normalise(p['number'])} for p in Employee.objects.values('id','name','number','direction')];cache.set(key,items,30)
    return items
def person_ids(query):
    words=normalise(query).split()
    return [p['id'] for p in directory() if words and all(w in p['normal'] or w in p['numeric'] for w in words)]
def matches_person_text(query,*values):
    words=normalise(query).split()
    return any(all(w in normalise(v) for w in words) for v in values)
def private(response):
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff';return response
def shell_context(request):
    return {'pmfe_ui_config':{'people':reverse('professional_people'),'notifications':reverse('professional_notifications')}}
@login_required
@require_GET
def people(request):
    scope()
    if not request.user.has_perm('training.view_employee'):raise PermissionDenied
    query=normalise(request.GET.get('q',''))[:80]
    if not query:return private(JsonResponse({'results':[]}))
    entries=directory()
    raw_ids=request.GET.get('ids','');allowed=None
    if raw_ids:
        parts=raw_ids.split(',')
        if len(parts)>1510 or len(raw_ids)>14000 or any(not v.isdecimal() for v in parts):return private(JsonResponse({'error':'Selecção inválida.'},status=400))
        allowed={int(v) for v in parts}
    words=query.split();matches=[]
    for p in entries:
        if allowed is not None and p['id'] not in allowed:continue
        if not all(w in p['normal'] or w in p['numeric'] for w in words):continue
        rank=0 if query==p['numeric'] else 1 if p['normal'].startswith(query) or p['numeric'].startswith(query) else 2
        matches.append((rank,p['normal'],p['id'],p))
    matches.sort(key=lambda r:r[:3]);results=[]
    for _,__,___,p in matches[:10]:results.append({k:p[k] for k in ['id','name','number','direction']}|{'url':reverse('employee_data',args=[p['id']])})
    return private(JsonResponse({'results':results,'more':len(matches)>10}))

def pending_for(user):
    if not user.is_active or not user.is_staff:return []
    items={};today=timezone.localdate()
    def add(kind,pk,title,detail,url):items[(kind,pk)]={'key':f'{kind}:{pk}','kind':kind,'title':title,'detail':detail,'url':url}
    if user.has_perm('training.view_traininggroup'):
        for g in TrainingGroup.objects.filter(state='Planeada',start__gte=today).select_related('course').order_by('start','pk'):
            add('scheduled',g.pk,'Formação agendada',f'{g.course.title} · {g.code} · {g.start:%d/%m/%Y}',reverse('group_detail',args=[g.pk]))
    can_read=user.has_perms(['training.view_training','training.view_employee'])
    if can_read:
        ended=list(TrainingGroup.objects.exclude(state='Cancelada').filter(end__lte=today).select_related('course'))
        group_ids=[g.pk for g in ended];by_group={}
        for t in Training.objects.filter(group_id__in=group_ids).only('pk','group_id','attendance','attendance_confirmed','source_data','status'):by_group.setdefault(t.group_id,[]).append(t)
        waiting=set(Enrollment.objects.filter(group_id__in=group_ids,state='Activa',training__isnull=True).values_list('group_id',flat=True))
        for g in ended:
            ts=by_group.get(g.pk,[])
            if any(not t.attendance_confirmed or t.attendance is None for t in ts) or g.pk in waiting:
                can_import=user.has_perms(['training.add_training','training.add_enrollment','training.change_traininggroup'])
                url=reverse('attendance_import',args=[g.pk]) if can_import else reverse('group_detail',args=[g.pk])
                add('attendance',g.pk,'Assiduidade por confirmar',f'{g.course.title} · {g.code}',url)
            if user.has_perm('training.validate_training_group') and any(t.status=='Concluído' and (not (t.source_data or {}).get('_group_validation_v1') or (t.source_data or {}).get('_group_validation_v1',{}).get('decisions',{}).get('academic') in ['Por validar','Pendente de avaliação']) for t in ts):
                add('validation',g.pk,'Validação pendente',f'{g.course.title} · {g.code}',reverse('group_validation')+f'?group=group:{g.pk}')
        from .query_helpers import dashboard_summary
        from .certificate_register import row_summary
        for t in dashboard_summary(Training.objects.filter(certificate_issued=True).select_related('employee','group','document_state')):
            state=getattr(t,'document_state',None);summary=row_summary(t)
            if t.delivery_reported or summary.get('delivered_count',0)>0 or summary.get('same_certificate_as') or state and state.learner_delivered is True:continue
            can_deliver=user.has_perm('training.change_trainingdocumentstate') and t.group_id is not None
            url=(reverse('planning_certificate_delivery',args=[t.pk])+f'?scope=group&group={t.group_id}') if can_deliver else reverse('employee_data',args=[t.employee_id])
            add('certificate',t.pk,'Certificado por entregar',f'{t.employee.name} · {t.title}',url)
    return list(items.values())
@login_required
@require_GET
def notifications(request):
    scope()
    if not request.user.is_staff:raise PermissionDenied
    values=pending_for(request.user)
    return private(JsonResponse({'count':len(values),'items':values[:50],'truncated':len(values)>50}))

def manual_data():return json.loads((Path(__file__).parent/'resources/user_manual.json').read_text('utf8'))
def manual_access(user):
    scope()
    if not user.is_active or not user.is_staff:raise PermissionDenied
@login_required
@require_GET
def manual(request):
    manual_access(request.user)
    return private(render(request,'assistant/manual.html',{'manual':manual_data()}))
@login_required
@require_GET
def manual_image(request,name):
    manual_access(request.user);data=manual_data();allowed={s['image'] for s in data['sections'] if s.get('image')}
    if name not in allowed:raise Http404
    path=Path(settings.BASE_DIR)/'manual_assets'/name
    if not path.is_file():raise Http404
    return private(FileResponse(path.open('rb'),content_type='image/png'))
def manual_bytes():
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Image,PageBreak
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from xml.sax.saxutils import escape
    from .executive_pdf import FONT_PATH
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if 'Manual' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('Manual',str(FONT_PATH/'Vera.ttf')))
    data=manual_data();out=io.BytesIO();doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,topMargin=22*mm,bottomMargin=20*mm,title=data['title'],author='PMFE')
    styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='ManualText',fontName='Manual',fontSize=9,leading=14,spaceAfter=8,textColor=colors.HexColor('#123149')))
    story=[Paragraph(escape(data['title']),styles['Title']),Paragraph(escape(data['intro']),styles['ManualText']),Paragraph('Versão isolada · 12/11/2026',styles['ManualText']),Spacer(1,12)]
    for s in data['sections']:story.append(Paragraph(f'<link href="#{s["id"]}">{escape(s["title"])}</link>',styles['ManualText']))
    story.append(PageBreak())
    for s in data['sections']:
        story.append(Paragraph(f'<a name="{s["id"]}"/>'+escape(s['title']),styles['Heading1']))
        for i,step in enumerate(s['steps'],1):story.append(Paragraph(f'{i}. '+escape(step),styles['ManualText']))
        if s.get('important'):story.append(Paragraph('Atenção: '+escape(s['important']),styles['ManualText']))
        path=Path(settings.BASE_DIR)/'manual_assets'/s.get('image','')
        if s.get('image') and path.is_file():
            w,h=ImageReader(str(path)).getSize();width=174*mm;height=min(175*mm,width*h/w);story.append(Image(str(path),width=height*w/h,height=height));story.append(Paragraph('Ecrã real da cópia isolada; exemplos de ensaio, identificação pessoal ocultada quando aplicável.',styles['ManualText']))
        story.append(Spacer(1,14))
    def footer(canvas,doc):canvas.setFont('Manual',8);canvas.drawString(18*mm,12*mm,'PMFE · Manual de Utilização');canvas.drawRightString(192*mm,12*mm,str(doc.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer);return out.getvalue()
@login_required
@require_GET
def manual_pdf(request):
    manual_access(request.user)
    return private(FileResponse(io.BytesIO(manual_bytes()),as_attachment=True,filename='PMFE_Manual_de_Utilizacao.pdf',content_type='application/pdf'))

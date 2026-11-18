from .professional_ui import matches_person_text
"""Read-only coverage: exact catalogue IDs, unique people, explicit denominator."""
from collections import defaultdict
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape
from django import forms
from django.db.models import F
from django.db.models.functions import JSONObject
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from .models import Course, Employee, Training, ConsolidatedParticipation, ConsolidatedEdition, Enrollment
from .consolidation import workers, enabled
from .widgets import date_widgets

STATES=[('', 'Todos os técnicos'),('participated','Já participaram'),('absent','Sem participação registada nesta formação'),('completed','Concluíram'),('not_completed','Não concluíram'),('pending','Por validar'),('no_training','Sem formação registada'),('registered','Inscritos, sem participação confirmada')]

class Filters(forms.Form):
    course=forms.ModelChoiceField(label='Formação',queryset=Course.objects.none(),required=False,empty_label='Todas as formações')
    catalogue_search=forms.CharField(label='Pesquisar formação por nome ou código',required=False,max_length=200)
    directions=forms.MultipleChoiceField(label='Direcções actuais (nenhuma seleccionada = todas)',required=False,widget=forms.CheckboxSelectMultiple)
    year=forms.IntegerField(label='Ano',required=False,min_value=1900,max_value=2200)
    start=forms.DateField(label='Início do período',required=False)
    end=forms.DateField(label='Fim do período',required=False)
    area=forms.ChoiceField(label='Categoria de competência',required=False)
    group=forms.ChoiceField(label='Grupo / edição',required=False)
    state=forms.ChoiceField(label='Estado de participação',choices=STATES,required=False)
    completion=forms.ChoiceField(label='Conclusão',choices=[('','Todos'),('completed','Concluído'),('not_completed','Não concluído'),('pending','Por validar')],required=False)
    q=forms.CharField(label='Pesquisar técnico por nome ou número',required=False,max_length=200)
    order=forms.ChoiceField(label='Ordenar direcções',choices=[('name','Nome'),('highest','Maior participação'),('lowest','Menor participação'),('uncovered','Mais técnicos sem participação')],required=False)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['course'].queryset=Course.objects.order_by('title','pk')
        self.fields['course'].label_from_instance=lambda c:(c.code or c.internal_code or 'Código por confirmar')+' · '+c.title
        self.fields['directions'].choices=[(x,x or 'Direcção por confirmar') for x in workers().values_list('direction',flat=True).distinct().order_by('direction')]
        self.fields['area'].choices=[('','Todas as categorias')]+[(x,x) for x in Course.objects.exclude(area='').values_list('area',flat=True).distinct().order_by('area')]
        self.fields['group'].choices=[('','Todos os grupos')]+[('c:'+str(pk),f'{title} · {group} · '+(start.strftime('%d/%m/%Y') if start else 'Data por confirmar')) for pk,title,group,start in ConsolidatedEdition.objects.filter(active=True).order_by('title','start').values_list('pk','title','group_label','start')]
        from .models import TrainingGroup
        self.fields['group'].choices += [('p:'+str(g.pk),str(g.code)) for g in TrainingGroup.objects.order_by('pk')]
        date_widgets(self.fields)
        for name,field in self.fields.items():field.widget.attrs['class']='coverage-direction-list' if name=='directions' else 'gf-field'
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and data['end']<data['start']:self.add_error('end','O fim não pode ser anterior ao início.')
        return data

def completion(p):
    from .operational_status import completion_assessment
    return {'Concluído':'completed','Não concluído':'not_completed'}.get(completion_assessment(p)['completion'],'pending')


def canonical_rows(people):
    ids=[e.pk for e in people]
    result=[]
    for p in ConsolidatedParticipation.objects.filter(active=True,employee_id__in=ids).select_related('edition__course').only('employee_id','edition_id','present_count','attendance_complete','attended_hours','attendance_percent','completed','grades','edition__course_id','edition__title','edition__group_label','edition__start','edition__end','edition__course__area','edition__course__source_metadata').annotate(_native_group_id=F('interface_link__training__group_id'),_operational_evidence=JSONObject(issues=F('evidence__issues'))):
        e=p.edition
        result.append({'employee_id':p.employee_id,'key':'c:'+str(p.pk),'course_id':e.course_id,'title':e.title,'area':e.course.area if e.course else '', 'group_key':'c:'+str(e.pk),'group':e.group_label,'start':e.start,'end':e.end,'hours':p.attended_hours,'attendance':p.attendance_percent,'effective':(p.present_count or 0)>0,'completion':completion(p),'grades':p.grades})
    # Unprojected manual records remain available without doubling canonical
    # imports. Exact course FK, never a fuzzy title equivalence.
    from django.db.models import Q
    qs=Training.objects.filter(Q(source_key='')|Q(source_key__startswith='manual:'),employee_id__in=ids,interface_link__isnull=True).select_related('group').only('pk','employee_id','course_id','group','title','area','start','end','attendance_confirmed','attendance','status','days','hours_per_day','grade','result','source_key','source_data')
    for t in qs:
        if t.source_key and not t.source_key.startswith('manual:'):continue
        present=bool(t.attendance_confirmed and (t.attendance or 0)>0)
        state='completed' if present and t.status=='Concluído' and t.result!='Reprovado' else 'not_completed' if t.status=='Desistência' or t.result=='Reprovado' else 'pending'
        result.append({'employee_id':t.employee_id,'key':'t:'+str(t.pk),'course_id':t.course_id,'title':t.title,'area':t.area,'group_key':'p:'+str(t.group_id) if t.group_id else '', 'group':t.group.code if t.group else 'Sem grupo','start':t.start,'end':t.end,'hours':t.completed_hours,'attendance':t.attendance_percent,'effective':present,'completion':state,'grades':[{'value':str(t.grade),'scale':'20'}] if t.grade is not None else []})
    for en in Enrollment.objects.filter(employee_id__in=ids,state='Activa',training__isnull=True).select_related('group__course'):
        g=en.group
        result.append({'employee_id':en.employee_id,'key':'e:'+str(en.pk),'course_id':g.course_id,'title':g.course.title,'area':g.course.area,'group_key':'p:'+str(g.pk),'group':g.code,'start':g.start,'end':g.end,'hours':None,'attendance':None,'effective':False,'completion':'pending','grades':[]})
    return result

def analyse(data,user=None):
    people=list(workers().only('pk','name','number','direction','category','review_status').order_by('name','number'))
    from . import data_validation
    if user is not None and data_validation.enabled() and not data_validation.can_review(user,'employee'):
        people=[p for p in people if data_validation.policy().ok('employee',p.pk,'identity')]
    if data.get('directions'):people=[p for p in people if p.direction in data['directions']]
    allrows=canonical_rows(people);allparticipated={r['employee_id'] for r in allrows if r['effective']}
    rows=allrows
    if data.get('course'):rows=[r for r in rows if r['course_id']==data['course'].pk]
    elif data.get('catalogue_search'):
        from django.db.models import Q
        q=data['catalogue_search']
        courses=set(Course.objects.filter(Q(title__icontains=q)|Q(code__icontains=q)|Q(internal_code__icontains=q)).values_list('pk',flat=True))
        rows=[r for r in rows if r['course_id'] in courses]
    if data.get('area'):rows=[r for r in rows if r['area']==data['area']]
    if data.get('year'):rows=[r for r in rows if r['start'] and r['start'].year==data['year']]
    if data.get('start'):rows=[r for r in rows if r['end'] and r['end']>=data['start']]
    if data.get('end'):rows=[r for r in rows if r['start'] and r['start']<=data['end']]
    if data.get('group'):rows=[r for r in rows if r['group_key']==data['group']]
    byperson=defaultdict(list)
    for r in rows:byperson[r['employee_id']].append(r)
    participated={r['employee_id'] for r in rows if r['effective']}
    completed={r['employee_id'] for r in rows if r['completion']=='completed'}
    failed={r['employee_id'] for r in rows if r['completion']=='not_completed'}-completed
    pending={r['employee_id'] for r in rows if r['completion']=='pending'}-completed-failed
    direction_rows=[]
    for direction in sorted({p.direction for p in people}):
        universe={p.pk for p in people if p.direction==direction};covered=len(universe&participated)
        direction_rows.append({'direction':direction or 'Direcção por confirmar','total':len(universe),'participated':covered,'not_participated':len(universe-participated),'completed':len(universe&completed),'rate':round(covered*100/len(universe),1) if universe else None})
    if data.get('order') in ['highest','lowest']:direction_rows.sort(key=lambda r:(r['rate'] or 0,r['direction']),reverse=data['order']=='highest')
    elif data.get('order')=='uncovered':direction_rows.sort(key=lambda r:(-r['not_participated'],r['direction']))
    selected={'participated':participated,'absent':{p.pk for p in people}-participated,'completed':completed,'not_completed':failed,'pending':pending,'no_training':{p.pk for p in people}-allparticipated,'registered':set(byperson)-participated}.get(data.get('state'),{p.pk for p in people})
    if data.get('completion'):selected &= {'completed':completed,'not_completed':failed,'pending':pending}[data['completion']]
    nominal=[]
    q=(data.get('q') or '').casefold()
    for p in people:
        if p.pk not in selected or q and not matches_person_text(q,p.name,p.number):continue
        editions=byperson[p.pk];hours=[r['hours'] for r in editions if r['hours'] is not None]
        nominal.append({'employee':p,'number':p.number,'name':p.name,'direction':p.direction or 'Por confirmar','category':p.category,'registry':p.review_status or 'Sem estado adicional','status':'Concluído' if p.pk in completed else 'Não concluído' if p.pk in failed else 'Por validar' if p.pk in pending else 'Sem participação registada','editions':editions,'hours':sum(hours,Decimal(0)) if hours else None,'effective':p.pk in participated})
    from .operational_status import from_participations,rows as operational_rows,summary as op_summary
    listed_editions=[e for person in nominal for e in person['editions']]
    canonical_ids=[int(r['key'][2:]) for r in listed_editions if r['key'].startswith('c:')]
    manual_ids=[int(r['key'][2:]) for r in listed_editions if r['key'].startswith('t:')]
    operational_items=from_participations(ConsolidatedParticipation.objects.filter(pk__in=canonical_ids))
    if manual_ids:operational_items+=operational_rows(training_ids=manual_ids)
    operational_summary=op_summary(operational_items)
    return {'operational_summary':operational_summary,'universe':len(people),'participated':len(participated),'not_participated':len(people)-len(participated),'completed':len(completed),'rate':round(len(participated)*100/len(people),1) if people else None,'directions':direction_rows,'nominal':nominal,'participations':len(rows),'distinct_courses':len({r['course_id'] for r in rows if r['course_id']}),'generated':timezone.localtime(),'course_label':str(data['course']) if data.get('course') else 'Todas as formações','filters':{k:str(v) for k,v in data.items() if v not in (None,'',[])},'denominator':'Técnicos oficiais cadastrados nas direcções actuais seleccionadas. Elegibilidade específica não registada.','historical_direction':'Direcção actual do cadastro; não se presume a direcção à data da formação.','no_training_total':len({p.pk for p in people}-allparticipated)}

def export(data,kind):
    headers=['N.º mecanográfico','Nome','Direcção actual','Categoria profissional','Situação','Estado cadastral','Horas registadas','Edições no filtro']
    nominal=[[r['number'],r['name'],r['direction'],r['category'],r['status'],r['registry'],r['hours'],len(r['editions'])] for r in data['nominal']]
    directions=[[r[k] for k in ['direction','total','participated','not_participated','completed','rate']] for r in data['directions']]
    dh=['Direcção','Técnicos','Participaram','Sem participação','Concluíram','Taxa (%)']
    summary=[['Formação',data['course_label']],['Filtros',str(data['filters'])],['Emissão',data['generated'].strftime('%d/%m/%Y %H:%M')],['Universo',data['universe']],['Participaram',data['participated']],['Sem participação',data['not_participated']],['Concluíram',data['completed']],['Taxa (%)',data['rate']],['Denominador',data['denominator']],['Referência direcção',data['historical_direction']]]
    for key in ['completed_confirmed','expected','issued','delivered','verify']:
        from .operational_status import LABELS
        summary.append([LABELS[key],data['operational_summary'][key]])
    from .man_days import coverage_metrics,export_rows
    summary.extend(export_rows(coverage_metrics(data['nominal'])))
    output=BytesIO()
    if kind=='xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font,PatternFill
        book=Workbook();book.remove(book.active)
        details=[]
        for r in data['nominal']:
            for e in r['editions']:details.append([r['number'],e['key'],e['title'],e['group'],e['start'].strftime('%d/%m/%Y') if e['start'] else 'Por confirmar',e['end'].strftime('%d/%m/%Y') if e['end'] else 'Por confirmar',e['hours'],e['attendance'],{'completed':'Concluído','not_completed':'Não concluído','pending':'Por validar'}[e['completion']],str(e['grades'])])
        for name,h,items in [('Resumo',['Indicador','Valor'],summary),('Direcções',dh,directions),('Lista nominal',headers,nominal),('Edições',['N.º mec.','ID exacto','Formação','Grupo','Início','Fim','Horas','Assiduidade (%)','Conclusão','Notas originais'],details)]:
            sheet=book.create_sheet(name);sheet.append(h)
            for row in items:sheet.append(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in row])
            sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
            for c in sheet[1]:c.font=Font(color='FFFFFF',bold=True);c.fill=PatternFill('solid',fgColor='10364D')
            for col in sheet.columns:sheet.column_dimensions[col[0].column_letter].width=28
        book.save(output);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:
        from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4,landscape
        from reportlab.lib.styles import getSampleStyleSheet
        styles=getSampleStyleSheet();normal=styles['Normal'];normal.fontSize=8;normal.leading=11
        para=lambda x:Paragraph(escape('—' if x is None else str(x)),normal)
        story=[Paragraph('EPAL — Participação na Formação',styles['Title'])]
        for a,b in summary:story.append(para(f'{a}: {b if b is not None else "Sem denominador"}'))
        def table(h,items,widths):
            t=LongTable([[para(v) for v in h]]+[[para(v) for v in row] for row in items],colWidths=widths,repeatRows=1)
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#D6E9EE')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#A5BAC6')),('BOTTOMPADDING',(0,0),(-1,-1),7)]));return t
        detail=[]
        for r in data['nominal']:
            for e in r['editions']:
                period=(e['start'].strftime('%d/%m/%Y') if e['start'] else 'Por confirmar')+' – '+(e['end'].strftime('%d/%m/%Y') if e['end'] else 'Por confirmar')
                detail.append([r['number'],e['title'],e['group'],period,e['hours'],str(e['attendance'])+'%' if e['attendance'] is not None else 'Por confirmar',{'completed':'Concluído','not_completed':'Não concluído','pending':'Por validar'}[e['completion']]])
        story += [Spacer(1,15),table(dh,directions,[230,80,100,120,90,100]),Spacer(1,15),table(headers,nominal,[55,140,145,100,100,80,50,50])]
        if detail:story += [Spacer(1,15),Paragraph('Detalhe das edições preservadas',styles['Heading2']),table(['N.º mec.','Formação','Grupo','Período','Horas','Assiduidade','Conclusão'],detail,[50,250,60,140,50,80,90])]
        SimpleDocTemplate(output,pagesize=landscape(A4),leftMargin=26,rightMargin=26,topMargin=25,bottomMargin=25).build(story)
        mime='application/pdf'
    response=HttpResponse(output.getvalue(),content_type=mime);response['Content-Disposition']=f'attachment; filename="EPAL_Participacao.{kind}"';return response

@login_required
@permission_required(['training.view_employee','training.view_training'],raise_exception=True)
@require_GET
def index(request):
    form=Filters(request.GET);valid=form.is_valid();data=analyse(form.cleaned_data,user=request.user) if valid else None
    kind=request.GET.get('export')
    if valid and kind in ['xlsx','pdf']:return export(data,kind)
    query=request.GET.copy();query.pop('page',None);query.pop('export',None)
    page=Paginator(data['nominal'] if data else [],40).get_page(request.GET.get('page'))
    from .participation_presentation import context
    return render(request,'participation_executive.html',{'form':form,'report':data,'query':query.urlencode(),**context(request,data)})

"""Offline vector PDF reports. All prose is derived from the selected data."""
import io, math
from pathlib import Path
from xml.sax.saxutils import escape
import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Circle, Wedge
from django.conf import settings
from .batch import EDITION_FIELDS
from .executive import COLORS
from .analytics import realised_hours

FONT_PATH=Path(reportlab.__file__).parent/'fonts'
for name,file in [('Exec','Vera.ttf'),('ExecBold','VeraBd.ttf')]:
    if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(FONT_PATH/file)))
BLUE=colors.HexColor('#103c62');TEAL=colors.HexColor('#148c9e');PALE=colors.HexColor('#eff6fa');GREY=colors.HexColor('#52697c')
WIDTH=A4[0]-36*mm
styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='ExecBody',fontName='Exec',fontSize=9,leading=14,textColor=BLUE,spaceAfter=8))
styles.add(ParagraphStyle(name='ExecSmall',parent=styles['ExecBody'],fontSize=7.5,leading=11,spaceAfter=0))
styles.add(ParagraphStyle(name='ExecTitle',fontName='ExecBold',fontSize=25,leading=30,textColor=BLUE,spaceAfter=12))
styles.add(ParagraphStyle(name='ExecHeading',fontName='ExecBold',fontSize=14,leading=20,textColor=BLUE,spaceBefore=8,spaceAfter=10,keepWithNext=True))
styles.add(ParagraphStyle(name='ExecWhite',parent=styles['ExecSmall'],textColor=colors.white,fontName='ExecBold'))

def fmt(value,digits=1):
    if value is None:return '-'
    return f'{float(value):,.{digits}f}'.replace(',',' ').replace('.',',')

def para(text,style='ExecBody'):return Paragraph(escape(str(text)),styles[style])

def table(headers,rows,widths=None):
    data=[[para(x,'ExecWhite') for x in headers]]+[[x if isinstance(x,Drawing) else para(x,'ExecSmall') for x in row] for row in rows]
    if len(data)==1:data.append([para('Sem registos.','ExecSmall')]+[para('','ExecSmall') for _ in headers[1:]])
    t=Table(data,colWidths=widths or [WIDTH/len(headers)]*len(headers),repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),BLUE),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,PALE]),('LINEBELOW',(0,0),(-1,-1),.35,colors.HexColor('#d3e0e8'))]))
    return t


def bar_chart(items,width=WIDTH,height=175):
    drawing=Drawing(width,height);left=35;bottom=32;plot_h=height-57;plot_w=width-left-12
    max_value=max((float(x['value']) for x in items),default=0) or 1
    for tick in range(5):
        y=bottom+plot_h*tick/4
        drawing.add(Line(left,y,width-5,y,strokeColor=colors.HexColor('#dce7ed'),strokeWidth=.5))
        drawing.add(String(left-5,y-2,fmt(max_value*tick/4,0),fontName='Exec',fontSize=7,textAnchor='end',fillColor=GREY))
    for i,item in enumerate(items):
        slot=plot_w/max(len(items),1);h=float(item['value'])/max_value*plot_h;x=left+i*slot+slot*.22
        drawing.add(Rect(x,bottom,slot*.56,h,fillColor=TEAL,strokeColor=None))
        drawing.add(String(x+slot*.28,bottom+h+5,fmt(item['value'],0),fontName='ExecBold',fontSize=7,textAnchor='middle',fillColor=BLUE))
        drawing.add(String(x+slot*.28,15,str(item['label'])[:12],fontName='Exec',fontSize=7,textAnchor='middle',fillColor=GREY))
    return drawing


def donut(items,total,width=WIDTH,height=175):
    d=Drawing(width,height);cx=105;cy=87;r=72;angle=90
    positive=[x for x in items if float(x['value'])>0]
    for i,item in enumerate(positive):
        extent=float(item['value'])/float(total)*360 if total else 0
        d.add(Wedge(cx,cy,r,angle-extent,angle,fillColor=colors.HexColor(item.get('color',COLORS[i%len(COLORS)])),strokeColor=colors.white,strokeWidth=1))
        angle-=extent
    if not positive:d.add(Circle(cx,cy,r,fillColor=PALE,strokeColor=None))
    d.add(Circle(cx,cy,47,fillColor=colors.white,strokeColor=None))
    d.add(String(cx,cy+2,fmt(total,0),fontName='ExecBold',fontSize=19,textAnchor='middle',fillColor=BLUE))
    d.add(String(cx,cy-14,'HORAS',fontName='Exec',fontSize=8,textAnchor='middle',fillColor=GREY))
    d.add(String(205,120,'Horas de participantes',fontName='ExecBold',fontSize=12,fillColor=BLUE))
    d.add(String(205,98,f'{len(positive)} áreas com carga horária',fontName='Exec',fontSize=9,fillColor=GREY))
    d.add(String(205,78,'Presenças x horas por dia',fontName='Exec',fontSize=9,fillColor=GREY))
    return d


class PagedCanvas(canvas.Canvas):
    def __init__(self,*args,meta=None,**kwargs):
        super().__init__(*args,**kwargs);self.states=[];self.meta=meta
    def showPage(self):self.states.append(dict(self.__dict__));self._startPage()
    def save(self):
        count=len(self.states)
        for state in self.states:
            self.__dict__.update(state);self.setStrokeColor(colors.HexColor('#cfdee7'));self.line(18*mm,17*mm,A4[0]-18*mm,17*mm)
            self.setFont('Exec',7);self.setFillColor(GREY);self.drawString(18*mm,12*mm,'EPAL / PROÁGUA - Base provisória')
            self.drawRightString(A4[0]-18*mm,12*mm,f'Pág. {self._pageNumber}/{count}')
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


def render_report(data,report_id='Pré-visualização',actor=''):
    output=io.BytesIO();doc=SimpleDocTemplate(output,pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,topMargin=38*mm,bottomMargin=24*mm,title='EPAL - Relatório Executivo de Formação',author=actor or 'EPAL / PROÁGUA')
    def header(c,doc):
        logo=settings.BASE_DIR/'static/pmf/epal-report-official.png'
        if logo.exists():c.drawImage(str(logo),18*mm,A4[1]-28*mm,width=50*mm,height=24*mm,mask='auto',preserveAspectRatio=True,anchor='c')
        c.setFillColor(BLUE);c.setFont('Exec',6)
        c.drawCentredString(43*mm,A4[1]-31*mm,'Empresa Pública de Água, EPAL - EP')
        c.setFillColor(BLUE);c.setFont('ExecBold',10);c.drawRightString(A4[0]-18*mm,A4[1]-17*mm,'RELATÓRIO EXECUTIVO DE FORMAÇÃO')
        c.setFont('Exec',7);c.setFillColor(GREY);c.drawRightString(A4[0]-18*mm,A4[1]-23*mm,f"Gerado em {data['generated_at']:%d/%m/%Y %H:%M} - Luanda")
        c.setStrokeColor(TEAL);c.line(18*mm,A4[1]-33*mm,A4[0]-18*mm,A4[1]-33*mm)
    story=[para('Relatório Executivo','ExecTitle'),para(data['period_label'],'ExecHeading'),para(data['selected'].get('direction') or 'Visão geral - todas as direcções')]
    story.append(para('Documento automático para acompanhamento e arquivo. Base provisória: aguarda validação dos Recursos Humanos da EPAL.'))
    if data['selected'].get('area') or data['selected'].get('status'):
        story.append(para('Filtros adicionais: área '+(data['selected'].get('area') or 'Todas')+'; situação '+(data['selected'].get('status') or 'Todas')+'.'))
    story.extend([Spacer(1,8),para('1. Síntese executiva','ExecHeading')])
    for text in data['insights']:story.append(para(text))
    cards=[]
    for item in data['kpis']:
        value=fmt(item['value'],1 if item['decimal'] else 0)+(' '+item['suffix'] if item['suffix'] else '')
        cards.append([para(item['label'],'ExecSmall'),para(value,'ExecHeading')])
    card_rows=[]
    for offset in (0,4):card_rows.append([Table([[a],[b]],colWidths=[WIDTH/4-16]) for a,b in cards[offset:offset+4]])
    grid=Table(card_rows,colWidths=[WIDTH/4]*4);grid.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PALE),('VALIGN',(0,0),(-1,-1),'TOP'),('BOX',(0,0),(-1,-1),.5,colors.HexColor('#cfdee7')),('INNERGRID',(0,0),(-1,-1),.5,colors.white),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]));story.extend([grid,Spacer(1,12)])
    story.append(para('Horas e participações acima referem-se a formações iniciadas no período. São horas de participantes, não a duração de sala de cada grupo.'))
    story.append(para(f"Média /20 (originais e calculadas): {fmt(data['average_final'])}/20, com {data['final_grades_count']} notas finais disponíveis. Cobertura do cadastro: {fmt(data['coverage'])}%."))
    if data['previous']:
        prev=data['previous'];change=fmt(prev['change'])+'%' if prev['change'] is not None else 'sem base percentual'
        story.append(para(f"Comparação com {prev['start']:%d/%m/%Y} a {prev['end']:%d/%m/%Y}: {fmt(prev['hours'])} horas e {prev['participations']} participações. Variação das horas: {change}."))
    story.append(para(f"Actividades datadas no período: {len(data['ended'])} participações concluídas pela data de fim; {len(data['deliveries'])} certificados com entrega confirmada e datada. Estes universos podem incluir formações iniciadas anteriormente."))
    story.extend([PageBreak(),para('2. Evolução e distribuição das horas','ExecHeading'),para(data['timeline_title']),bar_chart(data['timeline_bars']),Spacer(1,12),donut(data['area_bars'],data['stats']['hours'])])
    legend=[]
    for item in data['area_bars']:
        marker=Drawing(10,10);marker.add(Circle(5,5,4,fillColor=colors.HexColor(item['color']),strokeColor=None))
        legend.append([marker,item['label'],fmt(item['value']),fmt(item['percent'])])
    story.append(table(['Cor','Área de formação','Horas','Peso (%)'],legend,[WIDTH*.08,WIDTH*.56,WIDTH*.18,WIDTH*.18]))
    story.extend([PageBreak(),para('3. Direcções e resultados','ExecHeading'),para('A cobertura conta pessoas distintas com presença, divididas pelo cadastro da direcção. O cadastro não é reduzido pelo período ou pela área de formação.')])
    story.append(table(['Direcção','Cadastro','Com presença','Cobertura','Horas','Certif.'],[[d['label'],str(d['total']),str(d['present']),fmt(d['coverage'])+'%',fmt(d['hours']),str(d['certificate_value']) if d['certificate_value'] is not None else 'Por confirmar'] for d in data['directions_table']],[WIDTH*.35,WIDTH*.12,WIDTH*.14,WIDTH*.13,WIDTH*.13,WIDTH*.13]))
    story.extend([Spacer(1,12),table(['Resultado final','Participações concluídas'],[[x['label'],str(x['value'])] for x in data['results']],[WIDTH*.65,WIDTH*.35])])
    stats=data['stats']
    display=lambda value:'Por confirmar' if value is None else str(value)
    story.append(para(f"Certificados das participações iniciadas no período: emissão declarada {display(stats['issued_value'])}; entrega ao formando {display(stats['learner_delivery_value'])}; entrega aos RH {display(stats['hr_delivery_value'])}. A emissão não comprova entrega nem existência de PDF."))
    story.append(para(f"Cobertura de assiduidade: {stats['attendance_coverage']}/{stats['total']} participações; {stats['presences']}/{stats['attendance_days']} dias. Aproveitamentos percentuais na fonte: {stats['achievement_percent_count']}; notas finais identificadas: {stats['graded_count']}; equivalências calculadas: {stats.get('converted_evaluations',0)}. Equivalências /20 calculadas apenas para aproveitamentos finais comprovados, preservando o original."))
    priorities = (f"Rever {data['missing_final']} participações concluídas com nota ou resultado por completar." if data['missing_final'] else "As participações concluídas têm avaliação disponível.")
    priorities += (f" Confirmar {data['stats']['certificates_pending']} situações de entrega pendentes." if data['stats']['certificates_pending'] else " Não existem situações de entrega por confirmar neste universo.")
    story.extend([para('Prioridades de acompanhamento','ExecHeading'),para(priorities+" Os dados em falta permanecem separados dos resultados negativos.")])
    story.append(para('O impacto operacional no terreno será analisado quando o módulo de avaliação pós-formação/360º estiver ligado. As notas de formação não demonstram, por si só, melhoria operacional.'))
    story.extend([PageBreak(),para('4. Actividades e grupos do período','ExecHeading')])
    editions={}
    for row in data['rows']:
        key=tuple(getattr(row,f) for f in EDITION_FIELDS);item=editions.setdefault(key,{'row':row,'count':0,'hours':0});item['count']+=1;item['hours']+=realised_hours(row)
    story.append(table(['Formação / Formador(a)','Período','Inscrições','Horas de participantes'],[[x['row'].title+' / '+(x['row'].trainer or 'Por confirmar'),f"{x['row'].start:%d/%m/%y} - {x['row'].end:%d/%m/%y}",str(x['count']),fmt(x['hours'])] for x in editions.values()],[WIDTH*.48,WIDTH*.24,WIDTH*.12,WIDTH*.16]))
    story.extend([Spacer(1,12),para('5. Entregas de certificados datadas no período','ExecHeading'),para('Lista por data efectiva de entrega, independente da data de início da formação. Mantém os filtros de direcção, área e situação.')])
    story.append(table(['Técnico / N.º mecanográfico','Formação','Entrega / Referência'],[[r.employee.name+' / '+r.employee.number,r.title,f"{r.certificate_delivery_date:%d/%m/%y} / "+(r.certificate_reference or 'Sem referência')] for r in data['deliveries']],[WIDTH*.36,WIDTH*.4,WIDTH*.24]))
    story.append(para(f"Existem {data['delivery_dates_missing']} confirmações de entrega sem data no universo dos filtros organizacionais. Sem data, não é possível atribuí-las a um mês."))
    story.extend([Spacer(1,14),para('6. Critérios e rastreabilidade','ExecHeading'),para('Presenças x horas por dia determina a carga horária realizada. Assiduidade ponderada usa a soma de presenças e dias previstos e exclui formações planeadas. Registos em curso ou por confirmar tornam a assiduidade provisória. Aproveitamento usa apenas aprovados e reprovados de participações concluídas. Notas em falta ficam fora da média; nota zero permanece na média.')])
    story.append(para('A distribuição temporal é feita pela data de início; não é uma repartição diária das horas. A plataforma ainda não dispõe de registo diário que permita essa repartição. Cada grupo segue a identificação da grelha de actualização em lote. A contagem inclui edições históricas sem grupo associado no planeamento.'))
    archive_note = 'Pré-visualização não arquivada. Utilize Gerar, arquivar e descarregar PDF para conservar o documento.' if report_id == 'Pré-visualização' else 'O arquivo conserva este PDF e os filtros utilizados, mesmo que os registos sejam corrigidos posteriormente.'
    story.append(para(f'Identificador: {report_id}. Responsável pela geração: {actor or "Utilizador autenticado"}. {archive_note}','ExecSmall'))
    if data.get('historical_stats'):
        history=data['historical_stats']
        story.extend([PageBreak(),para('Grupos históricos e idade no início da formação','ExecHeading'),para('Filtros históricos: faixa '+str(data['selected'].get('age_band') or 'Todas')+'; acção '+str(data['selected'].get('action') or 'Todas')+'; edição '+str(data['selected'].get('edition') or 'Todas')+'.'),table(['Grupos confirmados','Participações','Trabalhadores únicos'],[[history['groups'],history['participations'],history['people']]]),table(['Faixa etária','Participações'],[[b['label'],b['count']] for b in history['ages']]),para('Assiduidade comprovada: '+(fmt(history['attendance'])+'%' if history['attendance'] is not None else 'Por confirmar')+f"; cobertura {history['attendance_coverage']}/{history['participations']}. Notas finais disponíveis: {history['grades']}. Emissão: {history['issued']}; entrega ao formando: {history['learner_delivered']}; entrega aos RH: {history['hr_delivered']}.")])
    doc.build(story,onFirstPage=header,onLaterPages=header,canvasmaker=lambda *a,**kw:PagedCanvas(*a,**kw))
    return output.getvalue()

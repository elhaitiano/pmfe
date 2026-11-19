from io import BytesIO
from html import escape
from decimal import Decimal
import re
from django.http import HttpResponse
from django.utils import timezone
from .direct_assessments import excel_text

def response(raw,kind,name):
    result=HttpResponse(raw,content_type='application/pdf' if kind=='pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    result['Content-Disposition']=f'attachment; filename="{name}.{kind}"';result['Cache-Control']='private, no-store';result['X-Content-Type-Options']='nosniff';return result

def safe_comment(text):
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[contacto omitido]',text)
    return re.sub(r'(?<!\d)(?:\+?244[ -]?)?9(?:[ -]?\d){8}(?!\d)','[contacto omitido]',text)

def export(summary,kind,title='Avaliação da Formação'):
    stamp=timezone.localtime().strftime('%d/%m/%Y | %H:%M')
    if kind=='xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font,PatternFill
        from openpyxl.workbook.properties import CalcProperties
        book=Workbook();book.calculation=CalcProperties(calcId=191029,fullCalcOnLoad=True)
        sheet=book.active;sheet.title='Resumo';sheet.append([excel_text(title),stamp]);sheet.append(['Participações registadas',summary['participants']]);sheet.append(['Fichas recebidas',summary['received']]);sheet.append(['Respostas válidas',summary['valid_responses']]);sheet.append(['Rascunhos excluídos',summary['drafts']]);sheet.append(['Percentagem','Conversão média /5 ×100; não é taxa de pessoas satisfeitas.'])
        sheet.append(['Dimensão','Respostas válidas','Média /5','Escala convertida (%)'])
        for d in summary['dimensions']:sheet.append([d['title'],d['n'],d['average'],d['percent']])
        rec=summary['recommendation'];sheet.append(['Recomendação — Sim (%)',rec['yes_percent'] if rec else None])
        sheet=book.create_sheet('Perguntas');sheet.append(['Pergunta','1 / Não','2','3','4','5 / Sim','Respostas válidas','Média /5','Escala convertida (%)','Fórmula verificável da média','Recomendação Sim (%)'])
        for i,q in enumerate(summary['questions'],2):
            formula=f'=IF(SUM(B{i}:F{i})=0,"",SUMPRODUCT(B{i}:F{i},{{1,2,3,4,5}})/SUM(B{i}:F{i}))'
            sheet.append([excel_text(q['text']),*q['counts'],q['n'],q['average'] if q['kind']=='rating' else None,q['percent'] if q['kind']=='rating' else None,formula if q['kind']=='rating' else None,q['yes_percent']])
        sheet=book.create_sheet('Grupos');sheet.append(['Grupo','Formação','Fichas recebidas','Método'])
        for g in summary['groups']:sheet.append([g['code'],excel_text(g['title']),g['received'],g['mode']])
        sheet=book.create_sheet('Sugestões');sheet.append(['Tipo','Texto sem associação nominal'])
        for c in summary['comments']:sheet.append([c['kind'],excel_text(safe_comment(c['text']))])
        for sheet in book:
            sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
            for cell in sheet[1]:cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='073B75')
            for col in sheet.columns:sheet.column_dimensions[col[0].column_letter].width=min(75,max(16,max(len(str(c.value or '')) for c in col)+2))
        out=BytesIO();book.save(out);return response(out.getvalue(),'xlsx','PMFE_Avaliacao_Formacao')
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.graphics.shapes import Drawing,Rect,String
    out=BytesIO();styles=getSampleStyleSheet();story=[Paragraph(escape(title),styles['Title']),Paragraph(stamp,styles['Normal']),Spacer(1,12)]
    story.append(Paragraph(f"Participações registadas: {summary['participants']} · Fichas recebidas: {summary['received']} · Respostas válidas: {summary['valid_responses']}",styles['Normal']))
    story.append(Paragraph('Percentagens: conversão da média /5 ×100, não percentagem de participantes satisfeitos.',styles['Normal']))
    drawing=Drawing(475,130)
    for i,d in enumerate(summary['dimensions']):
        y=110-i*30;value=float(d['percent'] or 0)
        drawing.add(String(0,y,d['title'],fontSize=8));drawing.add(Rect(200,y-2,200,11,fillColor=colors.HexColor('#eaf1fa'),strokeColor=None));drawing.add(Rect(200,y-2,value*2,11,fillColor=colors.HexColor('#087bbd'),strokeColor=None));drawing.add(String(407,y,'Sem dados' if d['average'] is None else f"{d['average']:.2f}/5",fontSize=8))
    story.append(Spacer(1,12));story.append(drawing)
    data=[['Pergunta','n','Média /5','% escala / Sim']]
    for q in summary['questions']:
        value=q['yes_percent'] if q['kind']=='yesno' else q['percent']
        data.append([Paragraph(escape(q['text']),styles['BodyText']),str(q['n']),'—' if q['average'] is None or q['kind']=='yesno' else f"{q['average']:.2f}",'Sem dados' if value is None else f'{value:.2f}%'])
    table=Table(data,colWidths=[325,32,60,70],repeatRows=1);table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#073B75')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#d5dfed')),('FONTSIZE',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story.append(table)
    if summary['comments']:
        story.append(Spacer(1,14));story.append(Paragraph('Sugestões — sem associação nominal',styles['Heading2']))
        for c in summary['comments']:story.append(Paragraph(escape(safe_comment(c['text'])),styles['BodyText']));story.append(Spacer(1,6))
    def footer(canvas,doc):canvas.setFont('Helvetica',8);canvas.drawString(40,24,'EPAL | PROÁGUA');canvas.drawRightString(555,24,f'Pág. {doc.page}')
    SimpleDocTemplate(out,pagesize=A4,leftMargin=40,rightMargin=40,topMargin=35,bottomMargin=40).build(story,onFirstPage=footer,onLaterPages=footer)
    return response(out.getvalue(),'pdf','PMFE_Avaliacao_Formacao')

"""Signed paper attendance lists, matching the supplied A4 landscape reference."""
import io, math
from pathlib import Path
from xml.sax.saxutils import escape
from django.conf import settings
import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph
from .analytics import format_phone

FONT_PATH=Path(reportlab.__file__).parent/'fonts'
for name,file in [('Attendance','Vera.ttf'),('AttendanceBold','VeraBd.ttf')]:
    if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(FONT_PATH/file)))
W,H=landscape(A4);LEFT=7*mm;WIDTH=W-14*mm
BLUE=colors.HexColor('#173b63');LINE=colors.HexColor('#63748b')
BODY=ParagraphStyle('AttendanceCell',fontName='Attendance',fontSize=7.2,leading=9,textColor=colors.black,splitLongWords=True)
HEAD=ParagraphStyle('AttendanceHead',parent=BODY,fontName='AttendanceBold',textColor=colors.white,fontSize=7.8,leading=10)
TITLE=ParagraphStyle('AttendanceTitle',parent=BODY,fontName='AttendanceBold',fontSize=9,leading=12)
META=ParagraphStyle('AttendanceMeta',parent=BODY,fontSize=8.5,leading=11)
COLS=[8,18,53,29,32,25,40,17,23,38]
COLS=[x/sum(COLS)*WIDTH for x in COLS]
LABELS=['#','N.º Mec.','Nome','Função','Área','Telemóvel','E-mail','Entidade','Hora chegada','Rubrica']


def p(text,style=BODY):return Paragraph(escape(str(text or '')),style)

def draw_para(c,para,x,y,width):
    _,height=para.wrap(width,H)
    para.drawOn(c,x,y-height)
    return height


def build_attendance(group,day,enrollments):
    buffer=io.BytesIO();c=Canvas(buffer,pagesize=(W,H));c.setTitle(f'PROÁGUA - Lista de presenças - {group.code}');c.setAuthor('PROÁGUA / EPAL')
    title=p(f'Título: {group.title} — {group.code}',TITLE)
    _,th=title.wrap(WIDTH*.62-5*mm,H)
    local=p('Local: '+group.location,META)
    _,lh=local.wrap(WIDTH*.62,H)
    header_bottom=H-28*mm-max(th+lh+3*mm,22*mm)-3*mm
    bottom=34*mm;head_h=12*mm;available=header_bottom-bottom-head_h
    if available<24*mm:raise ValueError('O título da formação é demasiado extenso para a lista. Utilize um título abreviado no catálogo e mantenha o conteúdo no campo Conteúdos.')
    prepared=[]
    for index,en in enumerate(enrollments,1):
        e=en.employee;source=e.source_data or {}
        values=[str(index),e.number,e.name,source.get('Função') or e.category,e.direction or e.area,format_phone(e.phone),e.email,'EPAL','','']
        cells=[p(v) for v in values];height=max(11*mm,max(cell.wrap(COLS[i]-3*mm,H)[1]+4*mm for i,cell in enumerate(cells)))
        if height>available:raise ValueError('Um campo do cadastro é demasiado extenso para imprimir numa linha. Reveja o nome, função, direcção ou contacto.')
        prepared.append((cells,height))
    pages=[];current=[];used=0
    for row in prepared:
        if current and (len(current)==12 or used+row[1]>available):pages.append(current);current=[];used=0
        current.append(row);used+=row[1]
    if current:pages.append(current)
    for page_num,rows in enumerate(pages,1):
        for file,x,width in [('attendance-suez.png',LEFT,30*mm),('attendance-proagua.png',LEFT+34*mm,23*mm)]:
            logo=settings.BASE_DIR/'static/pmf'/file
            if logo.exists():c.drawImage(str(logo),x,H-18*mm,width=width,height=10*mm,preserveAspectRatio=True,anchor='c',mask='auto')
        c.setFillColor(BLUE);c.setFont('AttendanceBold',13);c.drawRightString(W-LEFT,H-13*mm,'PROÁGUA - LISTA DE PRESENÇAS - FORMAÇÃO')
        c.setStrokeColor(BLUE);c.line(LEFT,H-23*mm,W-LEFT,H-23*mm)
        y=H-28*mm
        draw_para(c,p(f'Data: {day:%d/%m/%Y} | Horário: Início: {group.start_time:%H:%M} | Fim: {group.end_time:%H:%M}',META),LEFT,y,WIDTH*.38-4*mm)
        draw_para(c,p(f'Duração do dia: Formação: {group.hours_per_day:.1f} horas | Pausas: {group.break_hours:.1f} horas'.replace('.',','),META),LEFT,y-13*mm,WIDTH*.38-4*mm)
        draw_para(c,title,LEFT+WIDTH*.38,y,WIDTH*.62)
        draw_para(c,local,LEFT+WIDTH*.38,y-th-3*mm,WIDTH*.62)
        # Fixed headers and blank hand-signature columns. No confirmation notes are printed.
        y=header_bottom;c.setFillColor(BLUE);c.rect(LEFT,y-head_h,WIDTH,head_h,fill=1,stroke=0)
        x=LEFT
        for label,width in zip(LABELS,COLS):draw_para(c,p(label,HEAD),x+1.5*mm,y-2*mm,width-3*mm);x+=width
        y-=head_h
        visible=list(rows);space=available-sum(h for _,h in visible)
        while len(visible)<12 and space>=11*mm:
            visible.append(([p('') for _ in COLS],11*mm));space-=11*mm
        for cells,height in visible:
            x=LEFT;c.setStrokeColor(LINE);c.setLineWidth(.35)
            for i,(cell,width) in enumerate(zip(cells,COLS)):
                c.rect(x,y-height,width,height,fill=0,stroke=1)
                draw_para(c,cell,x+1.5*mm,y-2*mm,width-3*mm);x+=width
            y-=height
        c.setFillColor(BLUE);c.setFont('Attendance',7)
        c.drawString(LEFT,y-5*mm,'N.º Mec. = número mecanográfico — código de identificação do colaborador da EPAL.')
        c.setFont('AttendanceBold',8)
        c.drawString(LEFT+15*mm,22*mm,'Formador(es)');c.drawString(W*.59,22*mm,'Responsável, se aplicável')
        c.setFont('Attendance',7);c.drawString(LEFT+15*mm,17*mm,group.trainer[:70])
        c.setStrokeColor(LINE);c.line(LEFT+15*mm,11*mm,W*.44,11*mm);c.line(W*.59,11*mm,W-18*mm,11*mm)
        c.setFont('Attendance',7);c.drawRightString(W-LEFT,5*mm,f'Pág. {page_num}/{len(pages)}')
        c.showPage()
    c.save();return buffer.getvalue()

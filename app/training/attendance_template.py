from datetime import date
from decimal import Decimal
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

def make_workbook(group=None,*,sample_dates=None,people=None):
    wb=Workbook();cfg=wb.active;cfg.title='Formação';ws=wb.create_sheet('Assiduidade')
    days=list(group.sessions.order_by('date').values_list('date',flat=True)) if group else sample_dates or [date(2026,11,16),date(2026,11,17)]
    hours=group.hours_per_day if group else Decimal(7)
    values=[('Formação',group.course.title if group else ''),('Código da formação',group.course.code if group else ''),('Grupo',group.code if group else 'G1'),('Horas por dia',float(hours)),('Referência da lista','')]
    cfg.merge_cells('A1:F1');cfg['A1']='PMFE — Assiduidade por formação e grupo';cfg['A1'].font=Font(size=17,bold=True,color='FFFFFF');cfg['A1'].fill=PatternFill('solid',fgColor='123149');cfg.row_dimensions[1].height=32
    for row,(label,value) in enumerate(values,3):cfg.cell(row,1,label);cfg.cell(row,2,value);cfg.cell(row,1).font=Font(bold=True,color='123149')
    cfg['A10']='Como preencher';cfg['A10'].font=Font(bold=True,color='087F8C')
    instructions=['1. Abra o grupo no Planeamento e descarregue o seu modelo.','2. Indique o número mecanográfico; o nome é opcional.','3. Preencha todos os dias: P = presença; A = ausência.','4. Certificado obtido: Sim, Não ou em branco se desconhecido.','5. Para Sim, indique a referência administrativa do certificado/lista.','6. Importe o Excel, confira os resultados e confirme.','As presenças e horas são calculadas no Excel e conferidas na plataforma.','Certificado obtido não significa certificado entregue ao formando.','7 horas por dia por defeito; pode indicar 6 ou outra duração válida.']
    for r,text in enumerate(instructions,11):cfg.merge_cells(start_row=r,start_column=1,end_row=r,end_column=6);cfg.cell(r,1,text).alignment=Alignment(wrap_text=True);cfg.row_dimensions[r].height=30
    cfg.column_dimensions['A'].width=30;cfg.column_dimensions['B'].width=43
    for col in ['C','D','E','F']:cfg.column_dimensions[col].width=12
    hours_check=DataValidation(type='decimal',operator='between',formula1='.5',formula2='24');hours_check.errorTitle='Horas inválidas';hours_check.error='Use entre 0,5 e 24 horas.';hours_check.showErrorMessage=True;cfg.add_data_validation(hours_check);hours_check.add(cfg['B6'])
    ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=len(days)+8);ws['A1']='Registo de assiduidade';ws['A1'].font=Font(size=17,bold=True,color='FFFFFF');ws['A1'].fill=PatternFill('solid',fgColor='123149');ws.row_dimensions[1].height=32
    ws['A2']='Preencha apenas números mecanográficos, P/A e os campos do certificado. Não altere as colunas de cálculo.'
    headers=['Mecanográfico','Nome (opcional)']+days+['Certificado obtido','Referência','Presenças','Faltas','Assiduidade','Horas frequentadas']
    for c,label in enumerate(headers,1):
        cell=ws.cell(4,c,label);cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='087F8C');cell.alignment=Alignment(wrap_text=True,vertical='center')
        if isinstance(label,date):cell.number_format='dd/mm/yyyy'
    ws.row_dimensions[4].height=34
    if people is None:people=[e.employee for e in group.enrollments.filter(state='Activa').select_related('employee').order_by('employee__name')] if group else []
    n=max(30,len(people));last=get_column_letter(2+len(days));cert_col=3+len(days)
    pa=DataValidation(type='list',formula1='"P,A"',allow_blank=True);pa.error='Use P ou A.';pa.showErrorMessage=True;ws.add_data_validation(pa);pa.add(f'C5:{last}{n+4}')
    yn=DataValidation(type='list',formula1='"Sim,Não"',allow_blank=True);yn.error='Use Sim ou Não.';yn.showErrorMessage=True;ws.add_data_validation(yn);yn.add(f'{get_column_letter(cert_col)}5:{get_column_letter(cert_col)}{n+4}')
    for r in range(5,n+5):
        if r-5<len(people):
            person=people[r-5];ws.cell(r,1,person.number).number_format='@';name=person.name;ws.cell(r,2,"'"+name if name.startswith(('=','+','-','@')) else name)
        span=f'C{r}:{last}{r}';pcol=cert_col+2;acol=cert_col+3;percent=cert_col+4;hcol=cert_col+5
        for col,formula in [(pcol,f'=IF(A{r}="","",COUNTIF({span},"P"))'),(acol,f'=IF(A{r}="","",COUNTIF({span},"A"))'),(percent,f'=IF(A{r}="","",IF(COUNTIF({span},"P")+COUNTIF({span},"A")={len(days)},COUNTIF({span},"P")/{len(days)},"Por preencher"))'),(hcol,f'=IF(A{r}="","",IF(COUNTIF({span},"P")+COUNTIF({span},"A")={len(days)},COUNTIF({span},"P")*\'Formação\'!$B$6,"Por preencher"))')]:
            ws.cell(r,col,formula).fill=PatternFill('solid',fgColor='EAF0F5')
        ws.cell(r,percent).number_format='0.0%';ws.cell(r,hcol).number_format='0.00'
    ws.column_dimensions['A'].width=20;ws.column_dimensions['B'].width=42
    for c in range(3,len(headers)+1):ws.column_dimensions[get_column_letter(c)].width=18 if c<=2+len(days) else 23
    ws.freeze_panes='C5';ws.auto_filter.ref=f'A4:{get_column_letter(len(headers))}{n+4}';ws.sheet_view.showGridLines=False;cfg.sheet_view.showGridLines=False
    wb.calculation.fullCalcOnLoad=True;wb.calculation.forceFullCalc=True
    stream=BytesIO();wb.save(stream);return stream.getvalue()

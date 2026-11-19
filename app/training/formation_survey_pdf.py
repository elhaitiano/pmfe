from pathlib import Path
from io import BytesIO
import hashlib
from django.conf import settings
from django.core.exceptions import ValidationError,PermissionDenied
from pypdf import PdfReader,PdfWriter
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from .formation_survey_service import require,group_people

def source():
    return Path(__file__).parent/'resources/formation-evaluation-v1.pdf'

def form_pdf(user,kind='blank',group=None,employee_id=None,personal=False,contacts=False):
    require(user,'print_formation_evaluation')
    raw=source().read_bytes();reference=PdfReader(BytesIO(raw))
    if len(reference.pages)!=2:raise ValidationError('O modelo oficial deve ter duas páginas.')
    from .models import FormationQuestionnaire
    version=FormationQuestionnaire.objects.get(code='proagua-formation-v1')
    if hashlib.sha256(raw).hexdigest()!=version.template_digest:raise ValidationError('O PDF oficial foi alterado; interrompa e confira.')
    if kind=='blank':return raw
    if not group:raise ValidationError('Seleccione o grupo.')
    if personal and not user.has_perms(['training.print_personal_evaluation','training.view_employee']):raise PermissionDenied
    if contacts and not personal:raise ValidationError('Contactos exigem autorização nominal.')
    people=list(group_people(group)) if kind=='batch' else [None]
    if kind=='person':
        if not personal:raise PermissionDenied
        person=group_people(group).filter(pk=employee_id).first()
        if not person:raise ValidationError('O formando não pertence a este grupo.')
        people=[person]
    if kind=='group':people=[None]
    if len(people)>500:raise ValidationError('O lote excede 500 fichas; divida a impressão.')
    if not people:raise ValidationError('O grupo não tem participantes.')
    writer=PdfWriter();survey=getattr(group,'formation_survey',None);evaluation_date=survey.evaluation_date if survey else None
    if survey and personal and survey.anonymous:raise ValidationError('Este grupo utiliza avaliação anónima. Imprima sem identificação nominal.')
    if survey and survey.questionnaire_digest!=version.schema_digest:raise ValidationError('Esta versão exige o seu próprio modelo oficial de impressão.')
    for person in people:
        doc=PdfReader(BytesIO(raw));page=doc.pages[0];width,height=float(page.mediabox.width),float(page.mediabox.height)
        out=BytesIO();canvas=Canvas(out,pagesize=(width,height))
        def draw(text,x,top,w,h=18,size=10):
            if not text:return
            text=' '.join(str(text).split());size=min(size,w/max(stringWidth(text,'Helvetica',1),1))
            if size<6:raise ValidationError('Um campo é demasiado longo para o modelo oficial. Reveja-o antes de imprimir.')
            canvas.setFillColorRGB(1,1,1);canvas.rect(x,height-top-h,w,h,stroke=0,fill=1)
            canvas.setFillColorRGB(0,0,0);canvas.setFont('Helvetica',size);canvas.drawString(x+1,height-top-12,text)
        draw(evaluation_date.strftime('%d/%m/%Y') if evaluation_date else '',76,132,105)
        draw(group.location,333,132,220)
        draw(group.title+' — '+group.code,190,161,367,24,size=10)
        if person and personal:
            draw(person.name,246,282,308,18)
            draw(person.function,94,338,225,18)
            data=person.source_data or {}
            draw(data.get('Local de Trabalho') or data.get('Local de trabalho') or data.get('Local Trabalho'),445,310,109,18,size=8)
            if contacts:draw(person.email,86,310,239,18);draw(person.phone,401,338,153,18)
        canvas.save();overlay=PdfReader(BytesIO(out.getvalue()));page.merge_page(overlay.pages[0])
        writer.add_page(page);writer.add_page(doc.pages[1])
    result=BytesIO();writer.write(result);return result.getvalue()

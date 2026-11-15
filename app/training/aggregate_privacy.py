"""Conservative primary and complementary suppression of aggregate releases.

If a published partition has 1–4 people, withhold the entire aggregate release,
including its totals and drill-downs. Nominal workflows retain their own rights.
"""
from collections import defaultdict
from io import BytesIO
from django.http import HttpResponse
from django.shortcuts import render

MESSAGE='Agregação protegida: <5 pessoas. Totais, taxas, detalhes e gráficos omitidos para impedir identificação por diferença.'

def restricted(rows,employees,summary=None):
    rows=list(rows);employees=list(employees)
    small=lambda n:0<n<5
    ids={e.pk for e in employees}
    if small(len(ids)):return True
    partitions=defaultdict(set)
    for e in employees:
        for key in ('direction','category','gender'):
            partitions[('cadastro',key,getattr(e,key,'') or 'Por confirmar')].add(e.pk)
    for row in rows:
        partitions[('participações','all')].add(row.employee_id)
        for key in ('area','status','result','certified','certificate_obtained','certificate_issued'):
            partitions[('participações',key,str(getattr(row,key,None)))].add(row.employee_id)
        for key in ('direction','category','gender'):
            partitions[('participações',key,getattr(row.employee,key,'') or 'Por confirmar')].add(row.employee_id)
        partitions[('início',str(row.start)[:7])].add(row.employee_id)
        for key in ('certificate_obtained_date','certificate_issue_date','certificate_delivery_date'):
            if getattr(row,key,None):partitions[(key,str(getattr(row,key))[:7])].add(row.employee_id)
        # Published academic and reaction denominators must also meet the threshold.
        for relation in ('evaluation','reaction'):
            evidence=getattr(row,relation,None)
            if evidence:
                partitions[(relation,'respondentes')].add(row.employee_id)
                for key in ('validated_result','completion','logistics','organization','content','trainer','usefulness'):
                    value=getattr(evidence,key,None)
                    if value is not None:partitions[(relation,key,str(value))].add(row.employee_id)

        for attendance in row.session_attendances.select_related('session').all():
            partitions[('sessão',str(attendance.session.date)[:7])].add(row.employee_id)
    if summary:
        denominator=summary.metrics['coverage'].denominator
        if denominator is not None and small(denominator):return True
        present={r.employee_id for r in rows if r.pk in summary.effective_participations}
        if small(len(present)) or small(len(ids-present)):return True
    if any(small(len(group)) for group in partitions.values()):return True
    # A filtered release must not reveal a small excluded complement of the registry.
    from .models import Employee
    if ids and small(Employee.objects.exclude(pk__in=ids).count()):return True
    return False

def page(request,context,status=200):
    return render(request,'aggregate_restricted.html',{**context,'privacy_message':MESSAGE},status=status)

def pdf():
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate,Paragraph
    from xml.sax.saxutils import escape
    stream=BytesIO()
    SimpleDocTemplate(stream).build([Paragraph('Indicadores agregados',getSampleStyleSheet()['Title']),Paragraph(escape(MESSAGE),getSampleStyleSheet()['BodyText'])])
    return stream.getvalue()

def export(kind):
    if kind=='pdf':body=pdf();mime='application/pdf'
    elif kind=='xlsx':
        from openpyxl import Workbook
        book=Workbook();book.active.title='Protecção';book.active.append([MESSAGE]);stream=BytesIO();book.save(stream)
        body=stream.getvalue();mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:body='\ufeffEstado\n"'+MESSAGE+'"\n';mime='text/csv; charset=utf-8'
    response=HttpResponse(body,content_type=mime)
    response['Content-Disposition']=f'attachment; filename="indicadores-protegidos.{kind}"'
    return response

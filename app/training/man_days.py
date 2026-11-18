"""Equivalent man-days from verified imported attendance; no business writes."""
from datetime import date
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
import json
from .analytics import consolidated,confirmed

STANDARD_DAY=Decimal(7)

def metrics(rows):
    rows=list(rows)
    from .operational_kpis import attendance_summary
    allowed=attendance_summary(consolidated(rows)).hours_by_participation
    groups={};excluded=0
    for r in consolidated(list(rows)):
        source=r.indicator_source_data or {};marker=source.get('_attendance_import_v1')
        if r.pk not in allowed:excluded+=1;continue
        if not isinstance(marker,dict) or not marker.get('source_sha256') or not confirmed(r):
            excluded+=1;continue
        try:hours=Decimal(str(r.completed_hours))
        except (InvalidOperation,TypeError,ValueError):excluded+=1;continue
        if not hours.is_finite() or hours<0:excluded+=1;continue
        original=marker.get('row') if isinstance(marker.get('row'),dict) else {}
        if original.get('employee') is not None and original['employee']!=r.employee_id:excluded+=1;continue
        if isinstance(original.get('marks'),list):
            try:
                marks=original['marks'];rate=Decimal(str(marker.get('hours')))
                if any(m not in ['P','A'] for m in marks) or not rate.is_finite() or rate<=0 or sum(m=='P' for m in marks)!=r.attendance or Decimal(sum(m=='P' for m in marks))*rate!=hours:excluded+=1;continue
            except (InvalidOperation,TypeError,ValueError):excluded+=1;continue
        key=('group',r.group_id,r.employee_id) if r.group_id else ('record',r.pk)
        signature=json.dumps([str(hours),r.attendance,marker.get('hours'),marker.get('dates'),original.get('marks')],sort_keys=True,default=str)
        groups.setdefault(key,[]).append((r,hours,marker,signature))
    hours=Decimal(0);days=set();known=0;records=0;conflicts=0;duplicates=0
    for items in groups.values():
        if len({i[3] for i in items})>1:conflicts+=len(items);continue
        duplicates+=len(items)-1;r,h,marker,_=items[0];hours+=h;records+=1
        raw_dates=marker.get('dates');marks=(marker.get('row') if isinstance(marker.get('row'),dict) else {}).get('marks');daily=None
        if isinstance(raw_dates,list) and isinstance(marks,list) and len(raw_dates)==len(marks)>0:
            try:
                parsed=[date.fromisoformat(d) for d in raw_dates];daily_hours=Decimal(str(marker.get('hours')))
                if len(set(parsed))==len(parsed) and all(m in ['P','A'] for m in marks) and daily_hours.is_finite() and daily_hours>0 and sum(m=='P' for m in marks)==r.attendance and Decimal(sum(m=='P' for m in marks))*daily_hours==h and all(r.start<=d<=r.end for d in parsed):daily=[(r.employee_id,d) for d,m in zip(parsed,marks) if m=='P']
            except (TypeError,ValueError,InvalidOperation):pass
        if daily is not None:days.update(daily);known+=1
    return {'equivalent':(hours/STANDARD_DAY).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if records else None,'hours':hours if records else None,'effective_days':len(days) if records and known==records and not conflicts else None,'daily_records':known,'records':records,'excluded_records':excluded,'conflicting_records':conflicts,'duplicate_records_excluded':duplicates,'standard_hours':7}

def export_rows(metric):
    return [['Homens-Dia de Formação (H/D)',metric['equivalent']],['Horas de presença verificadas importadas',metric['hours']],['Dias de presença efectivos',metric['effective_days']],['Participações importadas incluídas em H/D',metric['records']],['Base de conversão H/D (h)',7]]

def coverage_metrics(nominal):
    from django.db.models import Q
    from .models import Training
    keys={e['key'] for person in nominal for e in person['editions']}
    canonical=[int(k[2:]) for k in keys if k.startswith('c:')];manual=[int(k[2:]) for k in keys if k.startswith('t:')]
    return metrics(Training.objects.filter(Q(pk__in=manual)|Q(interface_link__participation_id__in=canonical)).distinct())

def workbook(data):
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    from django.http import HttpResponse
    book=Workbook();sheet=book.active;sheet.title='Resumo';sheet.append(['Indicador','Valor'])
    for k in data['kpis']:sheet.append([k['label'],k['value']])
    for row in export_rows(data['man_days']):sheet.append(row)
    from .operational_kpis import certification_summary
    cert=certification_summary(data['rows'])
    sheet.append(['Certificados obtidos',cert['obtained'] if cert['obtained']+cert['not_obtained'] else 'Sem registo'])
    sheet.append(['Certificados emitidos',data['stats']['certificates_issued']])
    sheet.append(['Certificados entregues',data['stats']['certificates_reported']])
    sheet.append(['Emissão por confirmar',cert['issue_unknown']])
    sheet.append(['Taxa de emissão (%)',cert['issue_rate']['value']])
    sheet.append(['Taxa de entrega (%)',cert['delivery_rate']['value']])
    filters=book.create_sheet('Filtros');filters.append(['Filtro','Valor'])
    for key,value in data['selected'].items():filters.append([key,str(value) if value is not None else ''])
    filters.append(['Período',data['period_label']]);filters.append(['Critério temporal','Data de início da formação, como no Dashboard'])
    detail=book.create_sheet('Participações');detail.append(['TrainingID','N.º mecanográfico','Direcção','Categoria profissional','Género','Formação','Horas frequentadas','Homens-Dia (H/D)','Dias efectivos'])
    from .operational_kpis import attendance_summary
    selected=attendance_summary(data['rows']).hours_by_participation
    for row in data['rows']:
        m=metrics([row]) if row.pk in selected else {'equivalent':None,'effective_days':None};detail.append([row.pk,row.employee.number,row.employee.direction,row.employee.category,row.employee.gender,row.title,selected.get(row.pk),m['equivalent'],m['effective_days']])
    for tab in book:
        tab.freeze_panes='A2';tab.auto_filter.ref=tab.dimensions
        for col in tab.columns:tab.column_dimensions[col[0].column_letter].width=30
        for cell in tab[1]:cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='103C62')
        for row in tab:
            for cell in row:
                if isinstance(cell.value,str) and cell.value.startswith(('=','+','-','@','\t','\r')):cell.value="'"+cell.value
    out=BytesIO();book.save(out)
    return HttpResponse(out.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="EPAL_Resumo_Executivo.xlsx"'})

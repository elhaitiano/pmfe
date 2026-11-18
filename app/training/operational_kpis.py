"""Adapters for GitHub's pure KPI rules, preserving the existing 8088 schema.
Source: elhaitiano/pmfe main 8c126d7 (kpi_service and certification).
No document attachment or database write is required to calculate indicators.
"""
from types import SimpleNamespace
from decimal import Decimal
import json
from dataclasses import replace
from .git_kpi_service import Scope,aggregate_attendance
from .git_certification import certificate_indicators

def attendance_summary(rows,employees=None):
 from .analytics import confirmed
 rows=list(rows);adapted=[];seen={};proven_duplicates=0
 from .models import TrainingGroup
 groups=TrainingGroup.objects.in_bulk({r.group_id for r in rows if r.group_id})
 for row in rows:
  marker=(row.source_data or {}).get('_attendance_import_v1')
  if row.group_id and isinstance(marker,dict) and marker.get('source_sha256') and isinstance(marker.get('row'),dict):
   original=marker['row']
   if original.get('employee')==row.employee_id and isinstance(original.get('marks'),list):
    signature=json.dumps([row.employee_id,row.group_id,row.course_id,str(row.start),str(row.end),row.attendance,row.attendance_confirmed,str(row.hours_per_day),row.days,marker.get('hours'),marker.get('dates'),original.get('marks')],sort_keys=True,default=str)
    if signature in seen:proven_duplicates+=1;continue
    seen[signature]=row.pk

  adapted.append(SimpleNamespace(pk=row.pk,employee_id=row.employee_id,group_id=row.group_id,
   course_id=row.course_id,source_key=row.source_key,status=row.status,
   attendance_confirmed=confirmed(row),attendance=row.attendance,
   days=row.attendance_days,hours_per_day=row.hours_per_day,group=groups.get(row.group_id)))
 registry=list(employees) if employees is not None else [SimpleNamespace(pk=pk) for pk in {r.employee_id for r in rows}]
 result=aggregate_attendance(adapted,registry,scope=Scope({},'Selecção actual','all_records','current_registry' if employees is not None else 'selected_people'))
 return replace(result,duplicate_inputs=result.duplicate_inputs+proven_duplicates)

def certificate_obtained(row):
 source=getattr(row,'source_data',None) or {}
 marker=source.get('_attendance_import_v1')
 if not isinstance(marker,dict):return None
 original=marker.get('row')
 value=original.get('certificate') if isinstance(original,dict) else None
 return value if isinstance(value,bool) else None

def certification_summary(rows):
 rows=list({r.pk:r for r in rows}.values());adapted=[]
 from .analytics import document_value,documentary_states
 documentary_states(rows)
 for row in rows:
  adapted.append(SimpleNamespace(employee_id=row.employee_id,
   certificate_obtained=certificate_obtained(row),certificate_issued=document_value(row,'issued'),
   certified=True if row.delivery_reported else document_value(row,'learner_delivered'),certificate_issue_date=row.certificate_issue_date,
   certificate_delivery_date=row.certificate_delivery_date))
 return certificate_indicators(adapted)

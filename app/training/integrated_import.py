"""Import provisional project data; stable participation IDs, reviewed profile updates."""
import json,sqlite3
from datetime import datetime
from decimal import Decimal,InvalidOperation
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from .models import Employee,Training,AuditEvent

def import_integrated(workbook,apply,source):
    from .management.commands.import_excel import text,parse_date,integer,source_json
    report={'mode':'apply' if apply else 'dry-run','employees_created':0,'employees_updated':0,'trainings_created':0,'trainings_existing':0,'issues':[],'changes':[],'backup':None}
    if apply:
        db=Path(settings.DATABASES['default']['NAME']);backup=db.parent/'backups'/f'before-import-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3';backup.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(db) as a,sqlite3.connect(backup) as b:a.backup(b)
        report['backup']=str(backup)
    with transaction.atomic():
        sheet_name=next(n for n in ['FUNCIONARIOS_EPAL','BASE_EPAL','FUNCIONÁRIOS_ EPAL'] if n in workbook.sheetnames);s=workbook[sheet_name];header_row=11 if sheet_name=='FUNCIONÁRIOS_ EPAL' else 3;heads=next(s.iter_rows(min_row=header_row,max_row=header_row,values_only=True));seen=set();eligible=set()
        for i,row in enumerate(s.iter_rows(min_row=header_row+1,values_only=True),header_row+1):
            if not any(row):continue
            try:
                n=text(row[1]);name=text(row[3]);assert n and name,'Falta identidade'
                assert n not in seen,'Número mecanográfico duplicado';seen.add(n)
                e=Employee.objects.filter(number=n).first()
                if e and e.name.strip().casefold()!=name.casefold():raise ValueError('Nome diferente para número existente; revisão manual necessária')
                email=text(row[11])
                if email:
                    try:validate_email(email)
                    except ValidationError:report['issues'].append({'sheet':s.title,'row':i,'reason':'E-mail inválido preservado nos dados de origem; contacto não utilizado'});email=''
                fields=dict(name=name,birth_date=parse_date(row[4]),phone=text(row[8]),admission_date=parse_date(row[9]),category=text(row[10]),email=email,direction=text(row[12]),area=text(row[13]),source_data=source_json(heads,row))
                if 'Função' in fields['source_data']:
                    fields['function'] = text(fields['source_data']['Função'])
                if e:
                    changes={k:{'before':str(getattr(e,k)),'after':str(v)}for k,v in fields.items() if k!='source_data' and getattr(e,k)!=v}
                    if changes:report['changes'].append({'number':n,'fields':changes});report['employees_updated']+=1
                    for k,v in fields.items():setattr(e,k,v)
                else:e=Employee(number=n,**fields);report['employees_created']+=1
                e.full_clean();e.save();eligible.add(n)
            except (ValueError,AssertionError,ValidationError) as err:report['issues'].append({'sheet':s.title,'row':i,'reason':'Horas/Dia em falta ou inválidas' if isinstance(err,InvalidOperation) else str(err)})
        if 'BASE_MESTRA_FORMACAO' in workbook.sheetnames:
            s=workbook['BASE_MESTRA_FORMACAO'];heads=next(s.iter_rows(min_row=3,max_row=3,values_only=True));seen=set()
            for i,row in enumerate(s.iter_rows(min_row=4,values_only=True),4):
                if not any(row):continue
                try:
                    v=dict(zip(heads,row));pid=text(v['ID Participação']);assert pid and pid not in seen,'ID Participação em falta ou duplicado';seen.add(pid)
                    n=text(v['Nº Mecanográfico']);assert n in eligible,'Cadastro não validado nesta importação';e=Employee.objects.get(number=n);key='pilar3b:'+pid
                    existing=Training.objects.filter(source_key=key).first()
                    if existing:
                        if existing.employee_id!=e.pk:raise ValueError('Participação associada a outro funcionário')
                        report['trainings_existing']+=1;continue
                    title=text(v['Acção de Formação']);assert title,'Designação de formação em falta'
                    delivered=text(v.get('Certificado Entregue'));delivery=True if delivered=='Sim' else False if delivered=='Não' else None
                    t=Training(employee=e,title=title,area=text(v['Área/Categoria Formação']),trainer=text(v['Formador(a)']),provider=text(v['Entidade Formadora']),location=text(v['Local da Formação']),start=parse_date(v['Data de Início']),end=parse_date(v['Data de Fim']),days=integer(v['Duração (Dias)']),attendance=integer(v['Presenças']),hours_per_day=Decimal(text(v['Horas/Dia'])),grade=Decimal(text(v['Nota da Avaliação Final'])) if text(v.get('Nota da Avaliação Final')) else None,result=text(v.get('Resultado Final')) or 'Pendente',certified=delivery,certificate_reference=text(v.get('Nº/Ref. Certificado')),certificate_delivery_date=parse_date(v.get('Data de Entrega do Certificado')),observations=text(v.get('Observações')),status={'Concluída':'Concluído','Em curso':'Em Curso','Não iniciada':'Planeado','Desistência':'Desistência'}.get(text(v.get('Situação do Formando na Formação')),'Por confirmar'),source_key=key,source_data=source_json(heads,row))
                    t.full_clean();t.save();report['trainings_created']+=1
                except (ValueError,AssertionError,ValidationError,InvalidOperation,Employee.DoesNotExist) as err:report['issues'].append({'sheet':s.title,'row':i,'reason':'Horas/Dia em falta ou inválidas' if isinstance(err,InvalidOperation) else str(err)})
        if apply:AuditEvent.objects.create(actor='import_excel',action='IMPORT_INTEGRATED',detail=json.dumps(report,ensure_ascii=False))
        else:transaction.set_rollback(True)
    return report

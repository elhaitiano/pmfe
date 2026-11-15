"""Signed, optimistic, transactional daily attendance review; never infer academic results."""
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import ParseError
from openpyxl import Workbook, load_workbook
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from .models import AttendanceImport, SessionAttendance, TrainingGroup, AuditEvent

HEADERS = ['Grupo', 'N.º mecanográfico', 'Data', 'Sessão', 'Horas previstas', 'Marcação', 'Horas frequentadas']

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def number(value):
    try:
        result=Decimal(str(value).replace(',', '.'))
        if not result.is_finite():
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        raise ValidationError('Número de horas inválido.')

def template(group):
    book=Workbook()
    sheet=book.active;sheet.title='Presenças'
    sheet.append(HEADERS)
    for training in group.trainings.select_related('employee').order_by('employee__number'):
        for session in group.sessions.all():
            sheet.append([group.pk, training.employee.number, session.date.isoformat(), session.code,
                          session.duration, '', ''])
            sheet.cell(sheet.max_row,2).data_type='s'
            sheet.cell(sheet.max_row,4).data_type='s'
    sheet.freeze_panes='A2'
    for letter in 'ABCDEFG':sheet.column_dimensions[letter].width=24
    notes=book.create_sheet('Instruções')
    for text in ['P = presença integral, ou horas explícitas para presença parcial; A = ausência confirmada; ? = por verificar.',
                 'Linhas sem marcação nem horas ficam fora da importação. Não use fórmulas.',
                 'Não altere grupo, número mecanográfico, sessão ou horas previstas. Reveja primeiro o calendário na plataforma.',
                 'Notas, aprovação, conclusão e certificados não são deduzidos das presenças.']:
        notes.append([text])
    stream=BytesIO();book.save(stream);return stream.getvalue()

def parse(payload, group):
    if len(payload)>10*1024*1024:
        raise ValidationError('Tamanho máximo: 10 MB.')
    try:
        with ZipFile(BytesIO(payload)) as archive:
            if sum(i.file_size for i in archive.infolist())>30*1024*1024:
                raise ValidationError('O Excel expandido excede o limite.')
            if any('vbaproject' in n.lower() for n in archive.namelist()):
                raise ValidationError('Macros não são aceites.')
        book=load_workbook(BytesIO(payload), read_only=True, data_only=False)
    except (BadZipFile, ValueError, KeyError, ParseError) as exc:
        raise ValidationError('Excel inválido.') from exc
    try:
        sheet=book['Presenças']
        if sheet.max_row>2001 or sheet.max_column!=len(HEADERS):
            raise ValidationError('O modelo aceita até 2000 linhas e as sete colunas originais.')
        rows=list(sheet.iter_rows())
        if [c.value for c in rows[0]]!=HEADERS:
            raise ValidationError('Cabeçalho incompatível. Descarregue o modelo do grupo.')
        sessions={(s.date.isoformat(),s.code):s for s in group.sessions.all()}
        people={t.employee.number:t for t in group.trainings.select_related('employee')}
        parsed=[];seen=set()
        for index,cells in enumerate(rows[1:],2):
            if any(c.data_type=='f' for c in cells):
                raise ValidationError(f'Linha {index}: fórmulas não são aceites.')
            gid,employee,day,code,planned,mark,hours=[c.value for c in cells]
            if mark in (None,'') and hours in (None,''):
                continue
            if str(gid)!=str(group.pk):
                raise ValidationError(f'Linha {index}: grupo diferente.')
            employee=str(employee).strip()
            training=people.get(employee)
            if not training:
                raise ValidationError(f'Linha {index}: funcionário não inscrito neste grupo.')
            if isinstance(day,(date,datetime)):day=day.date().isoformat() if isinstance(day,datetime) else day.isoformat()
            session=sessions.get((str(day),str(code).strip()))
            if not session or session.duration is None or number(planned)!=session.duration:
                raise ValidationError(f'Linha {index}: sessão ou duração não corresponde ao calendário.')
            key=(training.pk,session.pk)
            if key in seen:raise ValidationError(f'Linha {index}: participação/sessão repetida.')
            seen.add(key)
            mark=str(mark or '').strip().upper()
            if mark not in ('P','A','?'):
                raise ValidationError(f'Linha {index}: use P, A ou ?.')
            actual=None if mark=='?' else (session.duration if mark=='P' else Decimal(0)) if hours in (None,'') else number(hours)
            if mark=='?' and hours not in (None,''):
                raise ValidationError(f'Linha {index}: horas desconhecidas devem ficar vazias.')
            if mark=='A' and actual!=0 or mark=='P' and actual<=0:
                raise ValidationError(f'Linha {index}: marcação e horas contraditórias.')
            record=SessionAttendance(session=session,training=training,hours=actual,confirmed=mark!='?',actor='preview')
            record.full_clean(validate_unique=False,validate_constraints=False)
            parsed.append({'training':training.pk,'session':session.pk,
                'hours':format(actual.normalize(),'f') if actual is not None else None,'confirmed':mark!='?',
                'date':session.date.isoformat(),'code':session.code,'duration':format(session.duration.normalize(),'f'),
                'number':employee})
        if not parsed:raise ValidationError('Nenhuma marcação para rever.')
        return sorted(parsed,key=lambda r:(r['training'],r['session']))
    except KeyError as exc:
        raise ValidationError('Folha Presenças em falta.') from exc
    finally:book.close()

def plan(group, rows):
    fingerprint=digest(rows)
    previous=AttendanceImport.objects.filter(group=group,fingerprint=fingerprint).first()
    changes=[];versions=[]
    for row in rows:
        session=group.sessions.get(pk=row['session'])
        training=group.trainings.select_related('employee').get(pk=row['training'])
        if session.duration is None or number(row['duration'])!=session.duration or session.date.isoformat()!=row['date'] or session.code!=row['code'] or training.employee.number!=row['number']:
            raise ValidationError('Identidade ou calendário alterado. Importe novamente.')
        existing=SessionAttendance.objects.filter(session=session,training=training).first()
        candidate=SessionAttendance(session=session,training=training,
            hours=number(row['hours']) if row['hours'] is not None else None,confirmed=row['confirmed'],actor='preview')
        candidate.full_clean(validate_unique=False,validate_constraints=False)
        before={'hours':str(existing.hours) if existing and existing.hours is not None else None,
                'confirmed':existing.confirmed if existing else False}
        after={'hours':row['hours'],'confirmed':row['confirmed']}
        identical=bool(existing and existing.hours==candidate.hours and existing.confirmed==candidate.confirmed)
        changes.append({'training':training.pk,'session':session.pk,'number':row['number'],'before':before,'after':after,
                        'operation':'repetição histórica' if previous else 'idêntico' if identical else 'correcção' if existing else 'inclusão'})
        versions.append([training.pk,training.version,session.pk,session.revision,
                         existing.revision if existing else 0,before])
    snapshot=digest([group.revision,group.state,versions])
    if group.state in ('Planeada','Cancelada'):
        raise ValidationError('Inicie um grupo válido antes de registar presenças.')
    return {'fingerprint':fingerprint,'snapshot':snapshot,'already':bool(previous),'changes':changes,
            'corrections':sum(c['operation']=='correcção' for c in changes)}

def apply(group_id, rows, expected, actor, filename, reason):
    if not reason.strip():raise ValidationError('Indique o motivo / referência da confirmação.')
    with transaction.atomic():
        # Reserve the SQLite writer before validating the reviewed snapshot.
        TrainingGroup.objects.filter(pk=group_id).update(revision=F('revision'))
        group=TrainingGroup.objects.get(pk=group_id)
        reviewed=plan(group,rows)
        if reviewed['already']:return reviewed
        if reviewed['snapshot']!=expected:raise ValidationError('Os dados mudaram desde a pré-visualização. Reveja novamente.')
        changed=[]
        for row,change in zip(rows,reviewed['changes']):
            if change['operation']=='idêntico':continue
            record=SessionAttendance.objects.filter(session_id=row['session'],training_id=row['training']).first()
            if record is None:record=SessionAttendance(session_id=row['session'],training_id=row['training'],actor=actor)
            record.hours=number(row['hours']) if row['hours'] is not None else None
            record.confirmed=row['confirmed'];record.actor=actor;record.source=reason
            record.revision+=1;record.full_clean();record.save()
            changed.append(change)
            group.trainings.filter(pk=row['training']).update(version=F('version')+1)
        item=AttendanceImport.objects.create(group=group,fingerprint=reviewed['fingerprint'],filename=filename[:255],
            actor=actor,reason=reason,payload=rows,changes=changed)
        TrainingGroup.objects.filter(pk=group.pk).update(revision=F('revision')+1)
        AuditEvent.objects.create(actor=actor,action='SESSION_IMPORT',detail=json.dumps({
            'import':item.pk,'group':group.pk,'reason':reason,'changes':changed},ensure_ascii=False))
        return reviewed

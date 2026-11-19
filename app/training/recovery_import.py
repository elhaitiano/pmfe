"""Apply an explicitly approved recovery plan, without mandatory PDF attachments.

No HTTP endpoint: callers must supply the reviewed, hashed plan and authorisation.
Existing enrolment, scheduling, audit and JSON evidence structures are reused.
"""
from datetime import date,time
from decimal import Decimal
import hashlib,json
import re
from django.core.exceptions import ValidationError

KIND='assisted-recovery'

def approved_handover(training):
    """Delivery to learner OR HR; never presume which recipient or its date."""
    if training.certified is False:return False
    marker=((training.indicator_source_data or {}).get('_certificate_summary') or {}).get('handover')
    if not isinstance(marker,dict) or training.certificate_issued is not True:return False
    from .models import DocumentaryRecord
    authority=DocumentaryRecord.objects.filter(pk=marker.get('authority_id'),kind='assisted-handover',state='applied').first()
    if not authority or digest(authority.payload)!=authority.payload_sha256 or marker.get('authority_digest')!=authority.payload_sha256:return False
    entry=authority.payload.get('entries',{}).get(str(training.pk))
    return bool(entry and entry.get('employee')==training.employee_id and entry.get('group')==training.group_id
        and entry.get('certificate_sha256')==marker.get('certificate_sha256') and entry.get('confirmed') is True
        and entry.get('recipient')=='learner_or_hr' and authority.payload.get('authorization'))

def confirm_handovers(bindings,authorization,actor):
    """Atomic, audited supplement to an existing reviewed recovery import."""
    if not bindings or not authorization.strip():raise ValidationError('Falta a confirmação explícita da entrega.')
    from copy import deepcopy
    from .models import Training,DocumentaryRecord,AuditEvent
    from .scheduling_guard import reservation_transaction
    request=dict(bindings={str(k):v for k,v in bindings.items()},authorization=authorization)
    logical=digest(request);key='handover:'+logical
    with reservation_transaction():
        if DocumentaryRecord.objects.filter(source_key=key,kind='assisted-handover',state='applied').exists():return dict(updated=0,already_applied=True)
        rows=list(Training.objects.filter(pk__in=bindings).order_by('pk'))
        if len(rows)!=len(bindings):raise ValidationError('Participação em falta.')
        entries={};old={}
        for t in rows:
            cert=(t.source_data or {}).get('_recovery_import_v1',{}).get('certificate') or {}
            if not cert.get('sha256') or cert['sha256']!=bindings[t.pk] or t.certificate_issued is not True:raise ValidationError('Certificado divergente ou não identificado; não confirmar.')
            if t.certified is False or t.certificate_hr_delivered is False:raise ValidationError('Existe uma declaração negativa; conferir antes de substituir.')
            entries[str(t.pk)]=dict(employee=t.employee_id,group=t.group_id,certificate_sha256=cert['sha256'],confirmed=True,recipient='learner_or_hr',delivery_date=None)
            old[str(t.pk)]=deepcopy(t.source_data)
        payload=dict(authorization=authorization,request_digest=logical,entries=entries)
        authority=DocumentaryRecord.objects.create(source_key=key,kind='assisted-handover',state='applied',payload=payload,payload_sha256=digest(payload),package_sha256=logical)
        for t in rows:
            data=deepcopy(t.source_data);summary=data.setdefault('_certificate_summary',{})
            summary['delivered_count']=1
            summary['handover']=dict(authority_id=authority.pk,authority_digest=authority.payload_sha256,certificate_sha256=bindings[t.pk])
            # Explicit handover confirms the certificate exists/was obtained,
            # without converting attendance into academic completion.
            data['_attendance_import_v1']['row']['certificate']=True
            t.source_data=data;t.full_clean();t.save(update_fields=['source_data'])
        AuditEvent.objects.create(actor=str(actor)[:150],action='CERTIFICATE_HANDOVERS_CONFIRMED',detail=json.dumps(dict(
            authority_id=authority.pk,authorization=authorization,entries=entries,before=old,recipient_not_inferred=True,delivery_dates_not_invented=True),ensure_ascii=False))
        return dict(updated=len(rows),already_applied=False,authority_id=authority.pk)
def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def approved_undated_receipt(training):
    """A bounded administrative exception; not a free JSON flag or fake date."""
    marker=(training.source_data or {}).get('_recovery_import_v1')
    if not isinstance(marker,dict) or not training.group_id or not training.employee_id:return False
    from .models import DocumentaryRecord
    authority=DocumentaryRecord.objects.filter(pk=marker.get('authority_id'),kind=KIND,state__in=['approved','applied']).first()
    if not authority or digest(authority.payload)!=authority.payload_sha256 or marker.get('authority_digest')!=authority.payload_sha256:return False
    decision=authority.payload.get('receipts',{}).get(f'{training.group_id}:{training.employee_id}')
    return bool(isinstance(decision,dict) and decision.get('confirmed') is True and decision.get('delivery_date_unknown') is True
        and decision.get('certificate_sha256') and decision.get('confirmation') and training.certified is True and training.certificate_issued is True)

def validate(payload):
    if payload.get('approved') is not True or not payload.get('authorization') or not payload.get('plan_sha256'):raise ValidationError('Falta a aprovação expressa da pré-visualização.')
    if not payload.get('groups'):raise ValidationError('Não há grupos para importar.')
    seen_groups=set();seen_people=set()
    for g in payload['groups']:
        key=(g['course'],g['number'])
        if key in seen_groups:raise ValidationError('Grupo repetido na preparação.')
        seen_groups.add(key)
        dates=[date.fromisoformat(d) for d in g['dates']]
        if not dates or dates!=sorted(set(dates)):raise ValidationError('Dias efectivos inválidos ou repetidos.')
        h=Decimal(g['hours_per_day']);breaks=Decimal(g['break_hours'])
        start=time.fromisoformat(g['start_time']);end=time.fromisoformat(g['end_time'])
        elapsed=Decimal((end.hour*60+end.minute)-(start.hour*60+start.minute))/60
        if h<=0 or breaks<0 or elapsed!=h+breaks:raise ValidationError('O horário não corresponde às horas efectivas e às pausas.')
        if not g['rows'] or len(g['rows'])>g['capacity']:raise ValidationError('Lotação inválida.')
        for row in g['rows']:
            k=(key,row['number'])
            if k in seen_people:raise ValidationError('Funcionário repetido no mesmo grupo.')
            seen_people.add(k)
            if len(row['marks'])!=len(dates) or any(m not in ['P','A'] for m in row['marks']):raise ValidationError('Presenças incompletas; não converter desconhecidos em faltas.')
            if row.get('obtained') not in [True,False,None] or row.get('delivered') not in [True,False,None]:raise ValidationError('Estado de certificado inválido.')
            if row.get('delivered') is True and not row.get('certificate'):raise ValidationError('O recebimento deve corresponder ao certificado identificado.')
    return digest(payload)

def apply(payload,actor):
    logical=validate(payload)
    from django.db.models import F
    from .models import Employee,Course,Trainer,TrainingGroup,Enrollment,Training,DocumentaryRecord,AuditEvent
    from .scheduling_guard import reservation_transaction
    from .scheduling import save_group
    from .interface_sync import reconcile
    with reservation_transaction():
        key='recovery:'+logical
        previous=DocumentaryRecord.objects.filter(source_key=key,kind=KIND,state='applied').first()
        if previous:
            if previous.payload.get('request_digest')!=logical:raise ValidationError('Rastreabilidade divergente.')
            bindings=list(Training.objects.filter(source_data___recovery_import_v1__authority_id=previous.pk).order_by('pk').values_list('pk',flat=True))
            if len(bindings)!=sum(len(g['rows']) for g in payload['groups']):raise ValidationError('O lote anterior foi alterado; conferir antes de repetir.')
            return dict(created=0,groups=previous.payload['group_ids'],training_ids=bindings,already_applied=True,record_id=previous.pk)
        numbers={r['number'] for g in payload['groups'] for r in g['rows']}
        people={e.number:e for e in Employee.objects.filter(number__in=numbers)}
        if len(people)!=len(numbers):raise ValidationError('Um funcionário não pertence ao cadastro actual. Não criar cadastro automaticamente.')
        def normal_phone(value):
            digits=re.sub(r'\D','',str(value or ''))
            if digits.startswith('00244') and len(digits)==14:digits=digits[5:]
            elif digits.startswith('244') and len(digits)==12:digits=digits[3:]
            return digits
        phone_owners={}
        for pk,primary,source_data in Employee.objects.values_list('pk','phone','source_data'):
            for contact_value in [primary,(source_data or {}).get('Telefone 2','')]:
                value=normal_phone(contact_value)
                if value:phone_owners.setdefault(value,set()).add(pk)
        group_ids=[];bound=[];changes=[]
        for g in payload['groups']:
            course=Course.objects.get(pk=g['course']);trainer=Trainer.objects.get(pk=g['trainer'])
            existing=TrainingGroup.objects.filter(course=course,number=g['number']).first()
            dates=[date.fromisoformat(d) for d in g['dates']]
            if existing:
                if existing.start!=dates[0] or existing.end!=dates[-1] or existing.trainer_record_id!=trainer.pk or list(existing.sessions.order_by('date').values_list('date',flat=True))!=dates:
                    raise ValidationError('O número do grupo existe com dados divergentes.')
                if existing.trainings.exists() or existing.enrollments.exists():raise ValidationError('O grupo já tem participantes fora deste lote; rever antes de actualizar.')
                group=existing
            else:
                if course.next_group!=g['number']:raise ValidationError('A numeração do catálogo mudou. Não reutilizar números nem criar duplicações.')
                values=dict(dates=dates,start=dates[0],end=dates[-1],trainer=trainer,provider=g.get('provider',''),location=g['location'],
                    capacity=g['capacity'],hours_per_day=Decimal(g['hours_per_day']),break_hours=Decimal(g['break_hours']),
                    start_time=time.fromisoformat(g['start_time']),end_time=time.fromisoformat(g['end_time']),planning_confirmation='Confirmado')
                group=save_group(course.pk,values,actor=actor)
            group_ids.append(group.pk)
            for row in g['rows']:
                person=people[row['number']]
                if person.name not in [row['registry_name'],row['confirmed_name']]:raise ValidationError('O nome cadastral mudou após a conferência.')
                if person.name!=row['confirmed_name']:
                    approved=payload.get('name_changes',{}).get(person.number)
                    if not approved or approved['before']!=person.name or approved['after']!=row['confirmed_name']:raise ValidationError('Correcção de nome sem confirmação expressa.')
                    changes.append(dict(employee=person.pk,number=person.number,field='name',before=person.name,after=row['confirmed_name']))
                    person.name=row['confirmed_name'];person.save(update_fields=['name'])
                contact=row.get('contact_change')
                if contact:
                    field=contact['field'];current=person.phone if field=='phone' else (person.source_data or {}).get('Telefone 2','')
                    if field not in ['phone','source_data.Telefone 2'] or current not in [contact['before'],contact['after']]:raise ValidationError('O contacto mudou após a pré-visualização; não substituir.')
                    value=contact['after']
                    if not value.isdecimal() or len(value)!=9:raise ValidationError('Contacto inválido.')
                    if phone_owners.get(value,set())-{person.pk}:raise ValidationError('O contacto está associado a outro funcionário; conferir.')
                    if current!=value:
                        changes.append(dict(employee=person.pk,number=person.number,field=field,before=current,after=value,source=row['source']))
                        if field=='phone':person.phone=value;person.save(update_fields=['phone'])
                        else:person.source_data={**(person.source_data or {}),'Telefone 2':value};person.save(update_fields=['source_data'])
                        phone_owners.setdefault(value,set()).add(person.pk)
                bound.append((g,group,row,person))
        receipts={}
        for g,group,row,person in bound:
            if row.get('delivered') is True:
                receipts[f'{group.pk}:{person.pk}']=dict(confirmed=True,delivery_date_unknown=True,
                    certificate_sha256=row['certificate']['sha256'],confirmation=row['delivery_confirmation'])
        authority_payload=dict(request_digest=logical,plan_sha256=payload['plan_sha256'],authorization=payload['authorization'],
            receipts=receipts,group_ids=group_ids,training_ids=[])
        authority=DocumentaryRecord.objects.create(source_key=key,kind=KIND,state='approved',payload=authority_payload,
            payload_sha256=digest(authority_payload),package_sha256=payload['plan_sha256'])
        ids=[]
        for g,group,row,person in bound:
            enrolment=Enrollment.objects.create(group=group,employee=person,state='Activa',confirmation='Confirmado',reason='Recuperação assistida expressamente aprovada pelo utilizador.')
            cert=row.get('certificate');source=dict(row['source'])
            marker=dict(employee=person.pk,number=person.number,marks=row['marks'],certificate=row.get('obtained'),reference='')
            data={'_attendance_import_v1':dict(row=marker,hours=g['hours_per_day'],dates=g['dates'],source_sha256=payload['attendance_sha256'],logical_digest=logical,certificate_states_version=2),
                '_recovery_import_v1':dict(authority_id=authority.pk,authority_digest=authority.payload_sha256,source=source,certificate=cert,
                    original_name=row['source_name'],confirmed_name=row['confirmed_name'],approval_status='approved',completion_not_inferred=True)}
            if cert:data['_certificate_summary']=dict(issued_count=1,delivered_count=int(row.get('delivered') is True),hours=cert['course_hours'],hours_records=1,authority=key)
            t=Training(employee=person,course=group.course,group=group,title=group.title,area=group.area,trainer_record=group.trainer_record,trainer=group.trainer,
                provider=group.provider,location=group.location,start=group.start,end=group.end,days=len(g['dates']),hours_per_day=Decimal(g['hours_per_day']),
                attendance=row['marks'].count('P'),attendance_confirmed=True,status='Por confirmar',result='Pendente',grade=None,
                certificate_issued=True if cert else None,certificate_issue_date=date.fromisoformat(cert['issue_date']) if cert else None,
                certified=True if row.get('delivered') is True else None,certificate_delivery_date=None,source_course_code=g['code'],
                source_key=f'manual:recovery:{logical[:16]}:{group.pk}:{person.pk}',source_data=data)
            t.full_clean();t.save();enrolment.training=t;enrolment.save(update_fields=['training']);ids.append(t.pk)
        # Keep the authority digest stable: training IDs are recorded separately in
        # the audit; an applied receipt remains verifiable after serialisation.
        authority.state='applied';authority.save(update_fields=['state'])
        reconcile(actor)
        for group_id in group_ids:
            TrainingGroup.objects.filter(pk=group_id).update(state='Concluída',revision=F('revision')+1)
        AuditEvent.objects.create(actor=str(actor)[:150],action='ASSISTED_RECOVERY_APPLIED',detail=json.dumps(dict(
            record=authority.pk,request_digest=logical,groups=group_ids,training_ids=ids,administrative_changes=changes,
            schedule=dict(start='08:30',end='16:30',break_hours=1,effective_hours=7),completion_not_inferred=True,
            delivery_dates_not_invented=True,private_documents_created=0),ensure_ascii=False))
        return dict(created=len(ids),groups=group_ids,training_ids=ids,already_applied=False,record_id=authority.pk)

"""Immutable evaluation supplement in the existing audit event store.

Training.grade is /20. Other scales belong in typed supplement events rather
than overwriting source_data or adding a misleading grade conversion.
"""
import hashlib,json
from decimal import Decimal,InvalidOperation
from pathlib import Path
from django.core.exceptions import ValidationError
from django.db import connection,transaction
from .models import Training,AuditEvent
from .historical_reconciliation import tablehash

ITEM='EPAL_EVALUATION_APPROVED';BATCH='EPAL_EVALUATION_BATCH'
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def binding(t,raw_source):
    protected=[t.source_key,t.employee_id,t.title,t.start.isoformat(),t.end.isoformat(),raw_source]
    return hashlib.sha256(json.dumps(protected,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
def validate(plan,expected_hash):
    if plan.get('plan_hash')!=expected_hash or digest({k:v for k,v in plan.items() if k!='plan_hash'})!=expected_hash:raise ValidationError('Plano/hash adulterado.')
    operations=plan['operations']
    if not operations or len({o['training_id'] for o in operations})!=len(operations):raise ValidationError('Operações vazias ou repetidas.')
    for op in operations:
        if op['expected_grade'] is not None:raise ValidationError('Uma nota existente não pode ser substituída.')
        if op['unit']=='percentage':
            try:value=Decimal(op['percentage'])
            except (InvalidOperation,TypeError):raise ValidationError('Percentagem inválida.')
            if not value.is_finite() or not 0<=value<=100:raise ValidationError('Percentagem fora da escala.')
        elif op['unit']=='qualitative':
            if op['raw'] not in ('Bom','Muito Bom','Excelente') or op['percentage'] is not None:raise ValidationError('Resultado qualitativo inválido.')
        else:raise ValidationError('Escala não documentada excluída.')
    for item in plan['sources']:
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:raise ValidationError('Fonte documental alterada.')
    return expected_hash

def apply(plan,backup,expected_hash,actor,fail_at=None):
    validate(plan,expected_hash)
    if plan['backup_sha256']!=backup['sha256']:raise ValidationError('Backup diferente do plano.')
    with transaction.atomic():
        # First write acquires SQLite's write lock before revalidation.
        header=AuditEvent.objects.create(actor=actor,action=BATCH,detail='pending')
        if AuditEvent.objects.filter(action=BATCH,detail__contains=expected_hash).exclude(pk=header.pk).exists():raise ValidationError('Lote já confirmado; não repetir.')
        for table,sha in backup['table_hashes'].items():
            if table not in ('auth_user','django_session') and tablehash(connection,table,header.pk)!=sha:raise ValidationError('Estado alterado desde o backup: '+table)
        existing=set()
        for raw in AuditEvent.objects.filter(action=ITEM).values_list('detail',flat=True):
            existing.add(json.loads(raw)['source_key'])
        event_ids=[]
        for i,op in enumerate(plan['operations']):
            t=Training.objects.select_related('employee').get(pk=op['training_id'])
            with connection.cursor() as cursor:
                cursor.execute('SELECT source_data FROM training_training WHERE id=%s',[t.pk]);source=cursor.fetchone()[0]
            if t.source_key in existing or t.source_key!=op['source_key'] or t.employee_id!=op['employee_id'] or t.version!=op['expected_version'] or t.grade is not None:raise ValidationError('Resultado existente, revisão ou identidade divergente.')
            if binding(t,source)!=op['signature'] or hashlib.sha256(str(t.employee.number).strip().encode()).hexdigest()!=op['employee_number_sha256']:raise ValidationError('Origem ou identidade divergente.')
            payload={**op,'plan_hash':expected_hash,'batch_event_id':header.pk,'approved':True,'previous_supplement':None,'grade_before':None,'grade_after':None,'execution':'Operação automatizada expressamente autorizada pelo responsável; não representa login interactivo.'}
            event=AuditEvent.objects.create(actor=actor,action=ITEM,detail=json.dumps(payload,ensure_ascii=False));event_ids.append(event.pk)
            if fail_at is not None and i==fail_at:raise ValidationError('Falha injectada na cópia isolada.')
        payload={'plan_hash':expected_hash,'evaluations':len(event_ids),'event_ids':event_ids,'backup':backup['backup'],'backup_sha256':backup['sha256'],'training_changes':0,'approval':plan['approval']}
        header.detail=json.dumps(payload,ensure_ascii=False);header.save(update_fields=['detail'])
        with connection.cursor() as cursor:
            cursor.execute('pragma integrity_check')
            if cursor.fetchall()!=[('ok',)]:raise ValidationError('Falha de integridade; rollback.')
            cursor.execute('pragma foreign_key_check')
            if cursor.fetchall():raise ValidationError('Falha de chaves estrangeiras; rollback.')
        if AuditEvent.objects.filter(pk__gt=header.pk,action=ITEM).count()!=len(event_ids):raise ValidationError('Auditoria incompleta; rollback.')
        return {'batch_event':header.pk,**payload}

def supplement_index():
    result={}
    for raw in AuditEvent.objects.filter(action__in=[ITEM,'EVALUATION_CORRECTION']).order_by('pk').values_list('detail',flat=True):
        try:
            value=json.loads(raw)
            if value.get('approved') and value.get('unit') in ('percentage','qualitative','grade20','none'):
                result[value.get('source_key') or f"manual:{value['training_id']}"]=value
        except (ValueError,KeyError,TypeError):continue
    return result

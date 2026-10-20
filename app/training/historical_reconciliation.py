"""Closed, atomic executor for the explicitly authorised Pilar 3B plan."""
import hashlib,json
from pathlib import Path
from django.core.exceptions import ValidationError
from django.db import connections,transaction
from .models import Training,TrainingDocumentState,AuditEvent
from .documents import save_delivery

APPROVAL='5fd274bd21c01da05c65e17d31e2beac2c713974d43f8a985974da9aeddb00df'
ACTION='PILAR_RECONCILIATION_BATCH'
AUTH_VOLATILE={'auth_user','django_session'}
EXCLUDED={'pilar3b:'+p for p in ['PF-00285','PF-00434','PF-00459','PF-01388','PF-01854','PF-00212','PF-00635','PF-01088','PF-01287','PF-01326','PF-01473','PF-01591','PF-02072','PF-01852']}

def filehash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()

def validate_plan(plan):
    data={k:v for k,v in plan.items() if k!='approval_hash'}
    h=hashlib.sha256(json.dumps(data,ensure_ascii=True,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    if h!=APPROVAL or plan.get('approval_hash')!=APPROVAL:raise ValidationError('Hash de aprovação inválido.')
    if len(plan['operations'])!=1617 or len({o['training_id'] for o in plan['operations']})!=1617:raise ValidationError('Âmbito do lote divergente.')
    for item in plan['source_manifest']:
        if filehash(item['path'])!=item['sha256']:raise ValidationError('Fonte documental alterada.')
    for op in plan['operations']:
        if op['source_key'] in EXCLUDED or set(op['proposed'])-{'attendance_confirmed','status'}:raise ValidationError('Operação fora do âmbito.')
        if op['proposed'].get('attendance_confirmed') is not True:raise ValidationError('Confirmação de assiduidade divergente.')
        if 'status' in op['proposed'] and (op['source_key']!='pilar3b:PF-02180' or op['proposed']['status']!='Concluído'):raise ValidationError('Conclusão não autorizada.')
    return h

def tablehash(connection,table,before_event=None):
    query='SELECT * FROM "'+table+'"'
    if table=='training_auditevent' and before_event is not None:query+=' WHERE id < '+str(int(before_event))
    query+=' ORDER BY rowid'
    with connection.cursor() as cursor:
        cursor.execute(query);rows=cursor.fetchall()
    def normal(v):
        if isinstance(v,bool):return int(v)
        if hasattr(v,'isoformat'):
            try:return v.isoformat(' ')
            except TypeError:return v.isoformat()
        return v
    h=hashlib.sha256()
    for row in rows:h.update((json.dumps([normal(v) for v in row],ensure_ascii=True,separators=(',',':'),default=str)+'\n').encode())
    return h.hexdigest()

def apply(plan,backup,user,fail_at=None,database='default'):
    approval=validate_plan(plan)
    for table,expected in plan['baseline_tables_sha256'].items():
        if table not in AUTH_VOLATILE and backup['table_hashes'].get(table)!=expected:raise ValidationError('Referência de negócio alterada antes do backup: '+table)
    connection=connections[database]
    with transaction.atomic(using=database):
        header=AuditEvent.objects.using(database).create(actor=user.get_username(),action=ACTION,detail='pending')
        if AuditEvent.objects.using(database).filter(action=ACTION,detail__contains=approval).exclude(pk=header.pk).exists():raise ValidationError('Lote já aplicado; repetição recusada.')
        for table,expected in backup['table_hashes'].items():
            if table not in AUTH_VOLATILE and tablehash(connection,table,header.pk)!=expected:raise ValidationError('Estado de negócio mudou: '+table)
        changed=[];issued=[]
        for index,op in enumerate(plan['operations']):
            t=Training.objects.using(database).get(pk=op['training_id'])
            if t.source_key!=op['source_key'] or t.version!=op['expected_version'] or any(getattr(t,k)!=v for k,v in op['before'].items()):raise ValidationError('Revisão/valores divergentes: '+op['source_key'])
            before={k:getattr(t,k) for k in op['proposed']}
            for key,value in op['proposed'].items():setattr(t,key,value)
            t.version+=1;t.full_clean()
            if Training.objects.using(database).filter(pk=t.pk,source_key=op['source_key'],version=op['expected_version'],**op['before']).update(**op['proposed'],version=t.version)!=1:raise ValidationError('Alteração concorrente.')
            AuditEvent.objects.using(database).create(actor=user.get_username(),action='HISTORICAL_RECONCILIATION',detail=json.dumps({'batch_event_id':header.pk,'plan_hash':approval,'training_id':t.pk,'source_key':t.source_key,'before':before,'after':op['proposed'],'version_before':op['expected_version'],'version_after':t.version,'evidence':op['evidence'],'execution':'Automated Codex operation expressly authorised by platform owner; not an interactive user confirmation.'},ensure_ascii=False,default=str))
            changed.append(t.pk)
            if op['document_state_proposal']:
                if TrainingDocumentState.objects.using(database).filter(training=t).exists() or t.certified is not None or t.certificate_reference or t.certificate_delivery_date:raise ValidationError('Estado documental posterior existente; não sobrescrever.')
                save_delivery(user,t,{'issued':'true','learner_delivered':'unknown','hr_delivered':'unknown','revision':0,'confirm':True,'reference':f"SHA256 {plan['source_sha256']}; PARTICIPACOES:{op['evidence']['participacoes_row']}; "+'; '.join(op['evidence']['source_keys']),'observations':'Emissão declarada explicitamente na fonte Pilar 3B. PDF não verificado. Entregas individuais por confirmar.','justification':'Consolidação automatizada autorizada: Sim literal na emissão de origem, sem fórmula e sem inferir entregas.'})
                issued.append(t.pk)
            if fail_at is not None and index==fail_at:raise ValidationError('Falha injectada apenas na cópia isolada.')
        if len(changed)!=1617 or len(issued)!=90:raise ValidationError('Totais de aplicação divergentes.')
        payload={'plan_hash':approval,'updated':len(changed),'attendance_confirmed':len(changed),'completion_changes':1,'issued':len(issued),'backup':backup['backup'],'backup_sha256':backup['backup_sha256'],'actor':user.get_username(),'execution':'Authorised automated reconciliation','audit_events_expected':1708,'training_ids':changed}
        header.detail=json.dumps(payload,ensure_ascii=False);header.save(update_fields=['detail'])
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA integrity_check')
            if cursor.fetchall()!=[('ok',)]:raise ValidationError('Integridade divergente: rollback obrigatório.')
            cursor.execute('PRAGMA foreign_key_check')
            if cursor.fetchall():raise ValidationError('Chaves estrangeiras divergentes: rollback obrigatório.')
        if AuditEvent.objects.using(database).count()!=backup['counts']['training_auditevent']+1708:raise ValidationError('Contagem de auditoria divergente.')
        return {**payload,'batch_event_id':header.pk}

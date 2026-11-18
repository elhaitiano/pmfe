"""Evidence-scoped policy. Disabled by default; no changes to original records.

Decisions are append-only AuditEvents. A private, hash-pinned baseline can supply
reviewed source evidence; changed rows invalidate their previous decisions.
"""
import contextvars,hashlib,json
from contextlib import contextmanager
from datetime import date,datetime
from pathlib import Path
from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured,PermissionDenied,ValidationError
from django.db import connections,transaction
from .models import AuditEvent,Employee,Training

ACTION='DATA_VERIFICATION'
STATES={'confirmed':'Confirmado por evidência','pending':'Pendente de validação','divergent':'Divergente','unrecorded':'Não registado','not_applicable':'Não aplicável'}
DIMENSIONS={'employee':('identity','roster','category','function'),'training':('participation','attendance','completion','evaluation','issued','learner_delivered','hr_delivered')}
TABLES={'employee':'training_employee','training':'training_training'}
_scope=contextvars.ContextVar('pmf_validation_scope',default=None)

def enabled():return bool(getattr(settings,'PMF_VALIDATION_ENABLED',False))
def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),default=lambda v:v.isoformat() if isinstance(v,(date,datetime)) else str(v))
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def raw_records(kind):
    with connections['default'].cursor() as c:
        c.execute('SELECT * FROM '+TABLES[kind]);names=[d[0] for d in c.description]
        return {r[0]:dict(zip(names,r)) for r in c.fetchall()}
def model(kind):
    if kind not in TABLES:raise ValidationError('Tipo de registo inválido.')
    return Employee if kind=='employee' else Training

class Policy:
    def __init__(self):
        self.rows={kind:raw_records(kind) for kind in TABLES}
        self.records={};path=getattr(settings,'PMF_VALIDATION_MANIFEST',None)
        if path:
            body=Path(path).read_bytes();expected=getattr(settings,'PMF_VALIDATION_MANIFEST_SHA256','')
            if not expected or hashlib.sha256(body).hexdigest()!=expected:raise ImproperlyConfigured('Manifesto de validação sem hash aprovado ou alterado.')
            value=json.loads(body)
            if value.get('schema')!=1 or not isinstance(value.get('records'),dict):raise ImproperlyConfigured('Manifesto de validação inválido.')
            self.records=value['records']
        self.events={}
        self.evaluations={}
        with connections['default'].cursor() as c:
            c.execute('SELECT * FROM training_trainingdocumentstate');names=[d[0] for d in c.description]
            self.documents={r[names.index('training_id')]:dict(zip(names,r)) for r in c.fetchall()}
        for event in AuditEvent.objects.filter(action__in=[ACTION,'EPAL_EVALUATION_APPROVED','EVALUATION_CORRECTION']).order_by('pk'):
            try:
                d=json.loads(event.detail)
                if event.action!=ACTION:
                    self.evaluations[d['training_id']]=[event.pk,event.detail];continue
                if d['kind'] in TABLES and d['dimension'] in DIMENSIONS[d['kind']] and d['state'] in STATES:self.events[(d['kind'],d['pk'],d['dimension'])]=(event.pk,d)
            except (ValueError,KeyError,TypeError):continue

    def fingerprint(self,kind,pk):return digest(self.rows[kind].get(pk))
    def dependency(self,pk,dimension):
        if dimension in ('issued','learner_delivered','hr_delivered'):return digest(self.documents.get(pk))
        if dimension=='evaluation':return digest(self.evaluations.get(pk))
        return None
    def decision(self,kind,pk,dimension):
        if dimension not in DIMENSIONS.get(kind,()):raise ValidationError('Dimensão inválida.')
        record=self.records.get(f'{kind}:{pk}',{})
        event=self.events.get((kind,pk,dimension));value=event[1] if event else record.get('states',{}).get(dimension,{})
        fingerprint=event[1].get('fingerprint') if event else record.get('fingerprint')
        evidence=value.get('evidence');state=value.get('state','pending')
        if fingerprint!=self.fingerprint(kind,pk) or not evidence or state not in STATES:state='pending'
        if kind=='training':
            row=self.rows[kind].get(pk,{})
            expected=value.get('employee_fingerprint') if event else record.get('employee_fingerprint')
            if expected!=self.fingerprint('employee',row.get('employee_id')):state='pending'
            current=self.dependency(pk,dimension)
            expected=value.get('dependency_fingerprint') if event else record.get('dependencies',{}).get(dimension)
            if current is not None and expected!=current:state='pending'
        return {'state':state,'label':STATES[state],'evidence':evidence,'revision':event[0] if event else 0}

    def ok(self,kind,pk,dimension):return self.decision(kind,pk,dimension)['state']=='confirmed'
    def participation(self,pk):
        row=self.rows['training'].get(pk,{})
        return self.ok('employee',row.get('employee_id'),'identity') and self.ok('training',pk,'participation') and not self.duplicate(pk)
    def fact(self,pk,dimension):return self.participation(pk) and self.ok('training',pk,dimension)
    def duplicate(self,pk):
        record=self.records.get(f'training:{pk}',{});proof=record.get('duplicate')
        if not proof or not proof.get('evidence') or record.get('fingerprint')!=self.fingerprint('training',pk):return False
        a=self.rows['training'].get(pk);b=self.rows['training'].get(proof.get('of'))
        return bool(a and b and proof.get('peer_fingerprint')==digest(b) and all(a[k]==b[k] for k in ('employee_id','title','start','end')))

def policy():return _scope.get() or Policy()
@contextmanager
def scope():
    token=_scope.set(Policy() if enabled() else None)
    try:yield
    finally:_scope.reset(token)

class ValidationMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        with scope():return self.get_response(request)

def official(rows):return [r for r in rows if policy().participation(r.pk)] if enabled() else list(rows)
def queryset(rows):return rows.filter(pk__in=[r.pk for r in official(list(rows))]) if enabled() else rows
def history_queryset(rows):
    from .analytics import EXCLUDED_KEYS
    return queryset(rows) if enabled() else rows.exclude(source_key__in=EXCLUDED_KEYS)
def workers(rows):return [e for e in rows if policy().ok('employee',e.pk,'identity') and policy().ok('employee',e.pk,'roster')] if enabled() else list(rows)
def worker_queryset(rows, scope='official'):
    """Shared selection; staff authorization belongs to the calling view."""
    from . import consolidation
    if consolidation.enabled() and scope=='official':return consolidation.workers(rows)
    if not enabled():return rows
    if scope=='official':return rows.filter(pk__in=[e.pk for e in workers(rows)])
    if scope not in ('pending','pending_historical','pending_conflict'):
        raise ValidationError('Consulta de trabalhadores inválida.')
    p=policy();ids=[]
    for e in rows:
        identity=p.ok('employee',e.pk,'identity')
        if identity and p.ok('employee',e.pk,'roster'):continue
        if scope=='pending_historical' and not identity:continue
        if scope=='pending_conflict' and identity:continue
        ids.append(e.pk)
    return rows.filter(pk__in=ids)

def fact(row,dimension):return not enabled() or policy().fact(row.pk,dimension)
def summary(rows):
    if not enabled():return None
    rows=list(rows);p=policy();eligible=[r for r in rows if p.participation(r.pk)]
    return {'physical':len(rows),'duplicates':sum(p.duplicate(r.pk) for r in rows),'confirmed':sum(p.participation(r.pk) for r in rows),'pending':sum(not p.participation(r.pk) and not p.duplicate(r.pk) for r in rows),
            'dimensions':{d:{s:sum(p.decision('training',r.pk,d)['state']==s for r in eligible) for s in STATES} for d in DIMENSIONS['training']}}
class DisplayRow:
    """Read-only presentation proxy; original ORM instance is never modified."""
    def __init__(self,row):self.original=row
    def __getattr__(self,name):return getattr(self.original,name)
    @property
    def attendance_confirmed(self):
        from .analytics import confirmed
        return confirmed(self.original)
    @property
    def completed_hours(self):return self.original.completed_hours if self.attendance_confirmed else None
    @property
    def attendance_percent(self):return self.original.attendance_percent if self.attendance_confirmed else None
    @property
    def grade(self):return self.original.grade if fact(self.original,'evaluation') else None
    @property
    def status(self):return self.original.status if self.original.status!='Concluído' or fact(self.original,'completion') else 'Pendente de validação'
    @property
    def result(self):return self.original.result if fact(self.original,'evaluation') else 'Pendente de validação'
    @property
    def certificate_label(self):
        from .analytics import document_value
        value=document_value(self.original,'learner_delivered')
        return 'Entregue' if value is True else 'Não entregue' if value is False else 'Por confirmar'
    @property
    def certificate_delivery_date(self):return self.original.certificate_delivery_date if fact(self.original,'learner_delivered') else None
def display(row):return DisplayRow(row) if enabled() else row
def display_rows(rows):return [display(r) for r in rows]
class DisplayEmployee:
    def __init__(self,employee):self.original=employee
    def __getattr__(self,name):return getattr(self.original,name)
    def field(self,name):
        value=getattr(self.original,name)
        return value+' (por validar)' if value and not policy().ok('employee',self.original.pk,name) else value
    @property
    def category(self):return self.field('category')
    @property
    def function(self):return self.field('function')
def display_employee(employee):return DisplayEmployee(employee) if enabled() else employee
def display_state(row,state):
    if not enabled() or state is None:return state
    import copy
    result=copy.copy(state)
    for dim in ('issued','learner_delivered','hr_delivered'):
        if not fact(row,dim):setattr(result,dim,None)
    for prefix,dim in [('learner','learner_delivered'),('hr','hr_delivered')]:
        if not fact(row,dim):setattr(result,prefix+'_date',None);setattr(result,prefix+'_responsible','')
    return result
def can_review(user,kind='training'):
    return user.is_authenticated and user.is_staff and user.has_perm('training.change_'+kind) and user.has_perm('training.view_'+kind) and (kind!='training' or user.has_perm('training.view_employee'))
def require_profile_access(user,employee):
    if enabled() and not policy().ok('employee',employee.pk,'identity') and not can_review(user,'employee'):raise PermissionDenied
def context(request):
    if not enabled():return {}
    employee=request.resolver_match.kwargs.get('pk') if request.resolver_match and request.resolver_match.url_name in ('employee_detail','employee_data','employee_print') else None
    review_kind='training' if can_review(request.user) else 'employee'
    result={'validation_enabled':True,'validation_can_review':can_review(request.user,review_kind),'validation_review_kind':review_kind}
    if employee:
        labels={'identity':'Identidade','roster':'Cadastro actual','category':'Categoria profissional','function':'Função exercida'}
        result['validation_employee_states']=[{'dimension':labels[d],**policy().decision('employee',employee,d)} for d in DIMENSIONS['employee']]
    return result

def preview(user,kind,pk,dimension,state,reason,evidence):
    if not enabled() or not can_review(user,kind):raise PermissionDenied
    if dimension not in DIMENSIONS.get(kind,()) or state not in STATES:raise ValidationError('Estado ou dimensão inválido.')
    if not reason.strip() or not evidence.strip():raise ValidationError('Justificação e referência documental obrigatórias.')
    p=Policy()
    if pk not in p.rows[kind]:raise ValidationError('Registo inexistente.')
    before=p.decision(kind,pk,dimension)
    payload={'kind':kind,'pk':pk,'dimension':dimension,'state':state,'reason':reason.strip(),'evidence':evidence.strip(),'before':before,'fingerprint':p.fingerprint(kind,pk),'employee_fingerprint':p.fingerprint('employee',p.rows[kind][pk]['employee_id']) if kind=='training' else None,'dependency_fingerprint':p.dependency(pk,dimension) if kind=='training' else None,'user':user.pk}
    return signing.dumps(payload,salt=ACTION,compress=True),payload

def confirm(user,token):
    if not enabled():raise PermissionDenied
    try:d=signing.loads(token,salt=ACTION,max_age=600)
    except signing.BadSignature:raise ValidationError('Proposta inválida ou expirada.')
    if not can_review(user,d['kind']) or d['user']!=user.pk:raise PermissionDenied
    with transaction.atomic():
        event=AuditEvent.objects.create(actor=user.get_username(),action=ACTION,detail='pending')
        p=Policy()
        if p.fingerprint(d['kind'],d['pk'])!=d['fingerprint'] or p.decision(d['kind'],d['pk'],d['dimension'])!=d['before']:raise ValidationError('Estado alterado ou confirmação já utilizada. Reabra a proposta.')
        if d['kind']=='training' and d['employee_fingerprint']!=p.fingerprint('employee',p.rows['training'][d['pk']]['employee_id']):raise ValidationError('Identidade alterada.')
        if d['kind']=='training' and d['dependency_fingerprint']!=p.dependency(d['pk'],d['dimension']):raise ValidationError('Avaliação ou estado documental alterado.')
        event.detail=canonical(d);event.save(update_fields=['detail'])
        return event

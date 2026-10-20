"""Individual, signed and audited corrections. No batch writes or new schema."""
import json
from decimal import Decimal
from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import connection,transaction
from .models import Training,AuditEvent
from .approved_evaluations import ITEM,binding

ACTION='EVALUATION_CORRECTION';SALT='pmf.individual-evaluation.v1';MAX_AGE=900
class CorrectionForm(forms.Form):
    unit=forms.ChoiceField(label='Tipo de resultado',choices=[('grade20','Nota original /20'),('percentage','Aproveitamento final (%)'),('qualitative','Avaliação qualitativa'),('none','Retirar resultado não comprovado')])
    value=forms.DecimalField(label='Valor numérico',required=False,max_digits=5,decimal_places=2,min_value=0,max_value=100)
    qualitative=forms.ChoiceField(label='Classificação',required=False,choices=[('','Seleccionar'),('Bom','Bom'),('Muito Bom','Muito Bom'),('Excelente','Excelente')])
    reason=forms.CharField(label='Motivo da correcção',min_length=8,max_length=1500,widget=forms.Textarea)
    reference=forms.CharField(label='Referência documental',min_length=3,max_length=500)
    final_achievement=forms.BooleanField(label='Confirmo que o valor numérico é uma avaliação final, não assiduidade ou satisfação',required=False)
    def clean(self):
        d=super().clean();unit=d.get('unit');value=d.get('value')
        if unit in ('grade20','percentage'):
            if value is None:self.add_error('value','Indique o valor comprovado.')
            elif unit=='grade20' and value>20:self.add_error('value','A escala /20 aceita apenas 0 a 20.')
            if not d.get('final_achievement'):self.add_error('final_achievement','Confirme o significado da avaliação.')
        elif unit=='qualitative' and not d.get('qualitative'):self.add_error('qualitative','Seleccione a classificação original.')
        return d
def require(user):
    if not user.is_authenticated or not user.is_staff or not user.has_perms(['training.change_training','training.view_training','training.view_employee']):raise PermissionDenied
def current(training):
    last=0
    for pk,raw in AuditEvent.objects.filter(action__in=[ITEM,ACTION]).order_by('pk').values_list('pk','detail'):
        data=json.loads(raw)
        if data.get('training_id')==training.pk:last=pk
    with connection.cursor() as c:
        c.execute('select source_data from training_training where id=%s',[training.pk]);source=c.fetchone()[0]
    return {'version':training.version,'grade':str(training.grade) if training.grade is not None else None,'event':last,'signature':binding(training,source)}
def normalised(data):
    unit=data['unit'];value=data.get('value')
    return {'unit':unit,'raw':str(value) if unit in ('grade20','percentage') else data.get('qualitative','') if unit=='qualitative' else '', 'percentage':str(value) if unit=='percentage' else None,'reason':data['reason'],'reference':data['reference'],'final_achievement':bool(data.get('final_achievement'))}
def preview(user,training,data):
    require(user)
    if training.status!='Concluído' and data['unit']!='none':raise ValidationError('Só é possível registar avaliações numa participação concluída.')
    payload={'user':user.pk,'training_id':training.pk,'before':current(training),'proposed':normalised(data)}
    return signing.dumps(payload,salt=SALT,compress=True),payload
def confirm(user,token,training_id):
    require(user)
    try:payload=signing.loads(token,salt=SALT,max_age=MAX_AGE)
    except signing.BadSignature:raise ValidationError('Confirmação inválida ou expirada. Prepare novamente a proposta.')
    if payload['user']!=user.pk or payload['training_id']!=training_id:raise PermissionDenied
    proposed=payload['proposed']
    form=CorrectionForm({'unit':proposed['unit'],'value':proposed['raw'] if proposed['unit'] in ('grade20','percentage') else '', 'qualitative':proposed['raw'] if proposed['unit']=='qualitative' else '', 'reason':proposed['reason'],'reference':proposed['reference'],'final_achievement':proposed['final_achievement']})
    if not form.is_valid():raise ValidationError('Proposta inválida.')
    with transaction.atomic():
        event=AuditEvent.objects.create(actor=user.get_username(),action='EVALUATION_PENDING',detail='pending')
        t=Training.objects.get(pk=payload['training_id']);before=current(t)
        if before!=payload['before']:raise ValidationError('A participação foi alterada ou a confirmação já foi utilizada. Reabra a página.')
        if t.status!='Concluído' and proposed['unit']!='none':raise ValidationError('Participação não concluída.')
        from .source_achievement import attach
        attach([t]);previous=t.source_achievement
        newgrade=Decimal(proposed['raw']) if proposed['unit']=='grade20' else None
        t.grade=newgrade;t.version+=1;t.full_clean()
        if Training.objects.filter(pk=t.pk,version=before['version']).update(grade=newgrade,version=t.version)!=1:raise ValidationError('Alteração concorrente.')
        detail={'training_id':t.pk,'source_key':t.source_key,'signature':before['signature'],'unit':proposed['unit'],'raw':proposed['raw'],'percentage':proposed['percentage'],'qualitative':proposed['unit']=='qualitative','approved':True,'corrected':True,'scope':'final_achievement','before':before,'previous_supplement':previous,'after':{'version':t.version,'grade':str(newgrade) if newgrade is not None else None},'reason':proposed['reason'],'reference':proposed['reference'],'original_result':previous.get('original_result',previous) if previous else None}
        event.action=ACTION;event.detail=json.dumps(detail,ensure_ascii=False,default=str);event.save(update_fields=['action','detail'])
        return event

import json
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from .models import TrainingGroup, Training, CertificateEvent
from .documents import CertificateForm
from .certificates import snapshot
from .views import staff_required, record
from .excel_sources import digest

class BatchCertificateForm(forms.Form):
    participants=forms.ModelMultipleChoiceField(label='Participações a confirmar',queryset=Training.objects.none())
    def __init__(self,*args,group,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['participants'].queryset=group.trainings.select_related('employee')
        import copy
        for name,field in CertificateForm.base_fields.items():
            if name!='revision':self.fields[name]=copy.deepcopy(field)

@login_required
@staff_required
def group_certificates(request,pk):
    group=get_object_or_404(TrainingGroup,pk=pk)
    form=BatchCertificateForm(request.POST if request.method=='POST' else None,group=group)
    preview=[];token=''
    if request.method=='POST' and form.is_valid():
        individual=[];before={}
        for row in form.cleaned_data['participants']:
            before[row.pk]=snapshot(row)
            payload=request.POST.copy();payload['revision']=str(row.version)
            item=CertificateForm(payload,instance=row)
            if not item.is_valid():
                form.add_error(None,f'Participação {row.pk}: {item.errors.as_text()}')
            individual.append(item)
        if not form.errors:
            expected={'user':request.user.pk,'group':pk,'digest':digest([
                [f.instance.pk,f.cleaned_data['revision'],{key:str(getattr(f.instance,key)) for key in f.Meta.fields},f.cleaned_data['reason']] for f in individual])}
            if request.POST.get('action')=='confirm':
                try:
                    if signing.loads(request.POST.get('token',''),salt='certificates-group',max_age=1200)!=expected:
                        raise ValidationError('Os destinatários ou valores mudaram. Reveja novamente.')
                    with transaction.atomic():
                        for item in individual:
                            row=item.instance
                            values={key:getattr(row,key) for key in item.Meta.fields}
                            if Training.objects.filter(pk=row.pk,version=item.cleaned_data['revision']).update(**values,version=row.version+1)!=1:
                                raise ValidationError('Uma participação mudou. Nenhum certificado do lote foi alterado.')
                            CertificateEvent.objects.create(training=row,actor=request.user.username,
                                reason=item.cleaned_data['reason'],before=before[row.pk],after=snapshot(row))
                            record(request,'UPDATE_CERTIFICATE',json.dumps({'training':row.pk,'before':before[row.pk],'after':snapshot(row),'group':pk},default=str))
                    return redirect('group_detail',pk=pk)
                except (ValidationError,signing.BadSignature) as exc:
                    form.add_error(None,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Revisão inválida ou expirada.')
            else:
                token=signing.dumps(expected,salt='certificates-group')
                labels={'certificate_obtained':'Obtido','certificate_issued':'Emitido','certified':'Entregue ao participante','certificate_hr_delivered':'Entregue aos RH'}
                def display(values):
                    return ' · '.join(f'{label}: '+('Sim' if values.get(key) is True else 'Não' if values.get(key) is False else 'Por confirmar') for key,label in labels.items())
                preview=[{'employee':item.instance.employee,'before':display(before[item.instance.pk]),'after':display(snapshot(item.instance)), 'dates':[(item.fields[key].label,getattr(item.instance,key)) for key in item.Meta.fields if key.endswith('_date')], 'responsible':item.instance.certificate_actor,'evidence':item.instance.certificate_evidence} for item in individual]
    return render(request,'certificate_batch.html',{'group':group,'form':form,'preview':preview,'token':token})

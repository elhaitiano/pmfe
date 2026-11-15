import json
from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, redirect, render
from .models import GroupSession, TrainingGroup
from .evaluations import conflict
from .views import staff_required, record
from .forms import StyledModelForm

class SessionForm(StyledModelForm):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo / referência', max_length=1000)
    confirm = forms.BooleanField(label='Confirmo a duração e os efeitos nas presenças desta sessão')
    class Meta:
        model = GroupSession
        fields = ['date', 'code', 'duration']
        widgets = {'date': forms.DateInput(attrs={'type':'date'})}
    def clean_duration(self):
        value = self.cleaned_data.get('duration')
        if value is None:
            raise forms.ValidationError('Indique a duração prevista.')
        return value

@login_required
@staff_required
def edit(request, pk, session_pk=None):
    group = get_object_or_404(TrainingGroup, pk=pk)
    instance = get_object_or_404(GroupSession, pk=session_pk, group=group) if session_pk else GroupSession(group=group, duration=7)
    old = model_to_dict(instance)
    records = list(instance.attendance_records.select_related('training__employee')) if instance.pk else []
    form = SessionForm(request.POST if request.method == 'POST' else None, instance=instance, initial={'revision':group.revision})
    if request.method == 'POST' and form.is_valid():
        if group.state == 'Cancelada':
            form.add_error(None, 'Grupo cancelado.')
        if records and any(form.cleaned_data[k] != old[k] for k in ('date','code')):
            form.add_error(None, 'A identidade de uma sessão com presenças é conservada.')
        if any(r.hours is not None and r.hours > form.cleaned_data['duration'] for r in records):
            form.add_error('duration', 'A duração não pode ser inferior às horas já verificadas.')
        if not form.errors:
            with transaction.atomic():
                if not TrainingGroup.objects.filter(pk=pk, revision=form.cleaned_data['revision']).update(revision=F('revision')+1):
                    return conflict()
                obj = form.save(commit=False)
                obj.revision += 1
                obj.save()
                record(request, 'SESSION_EDIT', json.dumps({'before':old, 'after':model_to_dict(obj),
                    'reason':form.cleaned_data['reason'], 'affected':[r.pk for r in records]}, default=str))
            return redirect('group_detail', pk=pk)
    return render(request, 'session_form.html', {'form':form, 'title':'Sessão e duração', 'records':records})

from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from . import session_import

class UploadForm(forms.Form):
    workbook = forms.FileField(label='Modelo de presenças preenchido (.xlsx)')

class ConfirmForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo / referência (obrigatório nas correcções)', max_length=1000)
    confirm = forms.BooleanField(label='Confirmo os valores e os efeitos apresentados')

@login_required
@staff_required
def attendance(request, pk):
    group=get_object_or_404(TrainingGroup,pk=pk)
    if request.method=='GET' and request.GET.get('download')=='1':
        response=HttpResponse(session_import.template(group),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition']=f'attachment; filename="presencas-grupo-{pk}.xlsx"'
        return response
    upload=UploadForm(request.POST or None, request.FILES or None)
    confirm=ConfirmForm(request.POST if request.POST.get('action')=='confirm' else None)
    preview=None;error=''
    if request.method=='POST':
        try:
            if request.POST.get('action')=='confirm' and confirm.is_valid():
                payload=signing.loads(confirm.cleaned_data['token'],salt='session-import',max_age=1200)
                if payload['user']!=request.user.pk or payload['group']!=pk:
                    raise ValidationError('A revisão pertence a outro utilizador ou grupo.')
                session_import.apply(pk,payload['rows'],payload['snapshot'],request.user.username,payload['filename'],confirm.cleaned_data['reason'])
                return redirect('group_attendance',pk=pk)
            elif request.POST.get('action')!='confirm' and upload.is_valid():
                file=upload.cleaned_data['workbook']
                if not file.name.lower().endswith('.xlsx'):
                    raise ValidationError('Utilize .xlsx sem macros.')
                rows=session_import.parse(file.read(10*1024*1024+1),group)
                preview=session_import.plan(group,rows)
                token=signing.dumps({'user':request.user.pk,'group':pk,'rows':rows,'snapshot':preview['snapshot'],'filename':file.name},salt='session-import',compress=True)
                confirm=ConfirmForm(initial={'token':token})
        except (ValidationError, signing.BadSignature, ValueError, KeyError) as exc:
            error=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Revisão inválida ou expirada; importe novamente.'
    records=group.attendance_imports.order_by('-created')[:30]
    return render(request,'session_import.html',{'group':group,'upload':upload,'confirm_form':confirm,'preview':preview,'error':error,'imports':records})

"""Explicit, audited admission of a legitimate repeat; no academic decisions."""
import json
from django import forms
from django.contrib.auth.decorators import login_required,permission_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction,IntegrityError,OperationalError
from django.db.models import F
from django.shortcuts import get_object_or_404,render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Employee,TrainingGroup,Enrollment,EligibilityDecision,Course,AuditEvent
from .eligibility import assess

SALT='pmfe.repeat-admission.v1'
class RepeatForm(forms.Form):
    employee=forms.ModelChoiceField(label='Formando',queryset=Employee.objects.none())
    reason=forms.CharField(label='Justificação da reciclagem/repetição',min_length=10,max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    reference=forms.CharField(label='Referência administrativa que sustenta a autorização',min_length=3,max_length=1000)
    confirm=forms.BooleanField(label='Confirmo a autorização específica e a inscrição nesta edição',required=False)
    token=forms.CharField(widget=forms.HiddenInput,required=False)
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        from .consolidation import workers
        self.fields['employee'].queryset=workers().order_by('name','number')
        for f in self.fields.values():
            if not isinstance(f.widget,(forms.HiddenInput,forms.CheckboxInput)):f.widget.attrs['class']='gf-field'

def preview(group,person,user):
    from .group_roster import group_trainings
    if not user.is_active or not user.is_staff or not user.has_perms(['training.authorize_training_repeat','training.add_enrollment','training.view_employee','training.view_training']):raise ValidationError('Sem permissão para autorizar e inscrever repetições.')
    from .consolidation import workers
    if not workers().filter(pk=person.pk).exists():raise ValidationError('O formando não pertence ao cadastro oficial activo.')
    if group.state!='Planeada' or group.end<timezone.localdate() or not group.course.can_create_groups:raise ValidationError('O grupo deve estar planeado, não terminado e pertencer a uma formação disponível.')
    if Enrollment.objects.filter(group=group,employee=person).exists() or group_trainings(group).filter(employee=person).exists():raise ValidationError('Este formando já se encontra inscrito nesta edição da formação. Não é possível efectuar uma inscrição duplicada.')
    item=assess(group.course,[person],group=group)[0]
    if item['other']:raise ValidationError('Já existe inscrição activa noutro grupo da mesma formação. Reveja-a antes de repetir.')
    if not item['proven']:raise ValidationError('A conclusão/participação anterior não está comprovada para esta formação. Requer análise individual, sem equivalência por nome.')
    if group.enrollments.filter(state='Activa').count()>=group.capacity:raise ValidationError('O grupo não tem vagas disponíveis.')
    return {'user':user.pk,'group':group.pk,'employee':person.pk,'revision':group.revision,'basis':item['basis'],'authorization_id':item['authorization'].pk if item['authorization'] else None,'warnings':item['warnings']}

@transaction.atomic
def apply(group,person,user,payload,reason,reference):
    # Course-level write serialises admissions across distinct groups on SQLite.
    Course.objects.filter(pk=group.course_id).update(next_group=F('next_group'))
    group=TrainingGroup.objects.select_related('course').get(pk=group.pk)
    fresh=preview(group,person,user)
    if fresh!=payload:raise ValidationError('Histórico, permissões ou grupo alterados. Refaça a pré-visualização.')
    if not reason or not 10<=len(reason.strip())<=1000 or not reference or not 3<=len(reference.strip())<=1000:raise ValidationError('Justificação e referência são obrigatórias, até 1.000 caracteres.')
    if TrainingGroup.objects.filter(pk=group.pk,revision=payload['revision'],state='Planeada').update(revision=F('revision')+1)!=1:raise ValidationError('Operação simultânea: refaça a pré-visualização.')
    decision=EligibilityDecision.objects.get(pk=payload['authorization_id'],course=group.course,employee=person,group=group,kind='repeat',basis=payload['basis']) if payload['authorization_id'] else EligibilityDecision.objects.create(course=group.course,employee=person,group=group,kind='repeat',basis=payload['basis'],reason=reason.strip(),reference=reference.strip(),actor=user)
    entry=Enrollment.objects.create(group=group,employee=person,reason=reason.strip())
    AuditEvent.objects.create(actor=user.get_username(),action='REPEAT_ADMISSION_CONFIRMED',detail=json.dumps({'group':group.pk,'employee':person.pk,'decision':decision.pk,'enrollment':entry.pk,'basis':payload['basis'],'reason':reason.strip(),'reference':reference.strip()},ensure_ascii=False))
    return entry

@login_required
@permission_required(['training.authorize_training_repeat','training.add_enrollment','training.view_employee','training.view_training'],raise_exception=True)
@require_http_methods(['GET','POST'])
def review(request,pk):
    group=get_object_or_404(TrainingGroup.objects.select_related('course'),pk=pk)
    if not request.user.is_active or not request.user.is_staff:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    available=group.state=='Planeada' and group.end>=timezone.localdate() and group.course.can_create_groups
    form=RepeatForm(request.POST or None);proposal=None;success=False;error='' if available else 'A reciclagem só pode ser preparada num grupo planeado, não terminado e de formação disponível.';status=200
    if request.method=='POST' and form.is_valid():
        try:
            person=form.cleaned_data['employee'];proposal=preview(group,person,request.user)
            if request.POST.get('action')=='confirm':
                supplied=signing.loads(form.cleaned_data['token'],salt=SALT,max_age=900)
                if not form.cleaned_data['confirm'] or supplied!=proposal:raise ValidationError('Confirme a pré-visualização exacta antes de inscrever.')
                apply(group,person,request.user,supplied,form.cleaned_data['reason'],form.cleaned_data['reference']);success=True;proposal=None
            elif request.POST.get('action')=='preview':form.fields['token'].initial=signing.dumps(proposal,salt=SALT);form.data=form.data.copy();form.data['token']=form.fields['token'].initial
            else:raise ValidationError('Acção inválida.')
        except (ValidationError,signing.BadSignature,IntegrityError,OperationalError) as exc:
            error=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Proposta expirada ou operação concorrente. Refaça a pré-visualização.';proposal=None;status=409
    return render(request,'planning/repeat_admission.html',{'group':group,'form':form,'proposal':proposal,'success':success,'error':error,'group_available':available},status=status)

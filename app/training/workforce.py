"""Exact approved denominators; no sums or inferred monthly averages."""
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from .models import WorkforceReference
from .views import staff_required, record

DIMENSIONS=('direction','category','gender')

def matches(reference,scope):
    directions=scope.filters.get('directions') or []
    direction=scope.filters.get('direction') or ''
    if directions:
        if len(directions)!=1 or direction and direction!=directions[0]:return False
        direction=directions[0]
    return (reference.approved and reference.start==scope.date_from and reference.end==scope.date_to
        and reference.direction==direction and all(getattr(reference,k)==(scope.filters.get(k) or '') for k in ('category','gender')))

def apply_reference(summary):
    ref=summary.scope.filters.get('reference')
    if not ref or not matches(ref,summary.scope):return summary
    metric=summary.metrics['coverage'];n=metric.numerator;d=ref.headcount
    value=(Decimal(n)*100/d).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if d and n is not None else None
    status='inconsistent' if n is not None and n>d else 'no_data' if not d else metric.status
    metrics={**summary.metrics,'coverage':replace(metric,value=value,denominator=d,status=status)}
    return replace(summary,metrics=metrics,scope=replace(summary.scope,reference=f'{ref} · efectivo {d} · responsável {ref.responsible} · {ref.source}'))

class ReferenceForm(forms.ModelForm):
    class Meta:
        model=WorkforceReference
        fields=['start','end','direction','category','gender','headcount','responsible','source','approved']
        widgets={k:forms.DateInput(attrs={'type':'date'}) for k in ('start','end')}
    def clean_approved(self):
        if not self.cleaned_data['approved']:raise forms.ValidationError('Confirme a aprovação antes de registar a referência.')
        return True

@login_required
@staff_required
def references(request):
    form=ReferenceForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        item=form.save(commit=False);item.actor=request.user.username;item.save()
        record(request,'DRH_REFERENCE',f'Referência {item.pk}: {item}')
        return redirect('workforce_references')
    return render(request,'workforce_references.html',{'form':form,'references':WorkforceReference.objects.all()[:100]})


def evidence_fingerprint():
    """Detect source changes without modifying a frozen report or exposing row data."""
    import hashlib,json
    from django.apps import apps
    payload={}
    for name in ('Employee','Training','TrainingGroup','GroupSession','SessionAttendance','TrainingEvaluation','GroupLogisticsEvaluation','WorkforceReference'):
        model=apps.get_model('training',name)
        payload[name]=list(model.objects.order_by('pk').values())
    return hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()

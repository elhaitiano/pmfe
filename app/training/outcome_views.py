from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, redirect, render
from .models import TrainingGroup, GroupLogisticsEvaluation
from .evaluations import RevisionForm, EVIDENCE, DATE_WIDGET, audited, conflict
from .views import staff_required

class LogisticsForm(RevisionForm):
    class Meta:
        model = GroupLogisticsEvaluation
        fields = ['mean', 'responses'] + EVIDENCE
        widgets = DATE_WIDGET

@login_required
@staff_required
def logistics(request, pk):
    group = get_object_or_404(TrainingGroup, pk=pk)
    instance = getattr(group, 'logistics_evaluation', None) or GroupLogisticsEvaluation(group=group)
    before = model_to_dict(instance)
    form = LogisticsForm(request.POST if request.method == 'POST' else None, instance=instance,
                         initial={'revision': group.revision})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if not TrainingGroup.objects.filter(pk=pk, revision=form.cleaned_data['revision']).update(revision=F('revision')+1):
                return conflict()
            obj = form.save(commit=False)
            obj.revision += 1
            obj.save()
            audited(request, 'GROUP_LOGISTICS', obj, before, form.cleaned_data['correction_reason'])
        return redirect('group_detail', pk=pk)
    return render(request, 'form.html', {'form':form, 'title':'Avaliação logística do grupo'})

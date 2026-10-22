"""Catalogue management, private 360 responses, and development follow-up."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import F, Q
from django.forms import inlineformset_factory, model_to_dict
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import StyledModelForm
from .models import Employee, Training, TrainingGroup, Course
from .competency_models import (Competency, RoleProfile, RoleCompetency, EvaluationCycle,
    CycleCompetency, CycleRater, CompetencyResponse, DevelopmentAction, PrivateResponseRevision)
from .competency_services import lock_cycle, open_cycle, finish_cycle, cycle_results, RevisionConflict
from .views import staff_required, record

DATE = lambda: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})


class EditForm(StyledModelForm):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo da gravação / correcção', max_length=1000)


class CompetencyForm(EditForm):
    class Meta:
        model = Competency
        fields = ['name','dimension','description','active']

    def clean_name(self):
        return self.cleaned_data['name'].strip()


class RoleForm(EditForm):
    class Meta:
        model = RoleProfile
        fields = ['title','description','active']

    def clean_title(self):
        return self.cleaned_data['title'].strip()


class RuleForm(StyledModelForm):
    class Meta:
        model = RoleCompetency
        fields = ['competency','expected','weight','validated','validated_by','validated_on']
        widgets = {'validated_on': DATE()}


RuleSet = inlineformset_factory(RoleProfile, RoleCompetency, form=RuleForm,
    extra=3, can_delete=True, max_num=100, validate_max=True)


class CycleForm(EditForm):
    class Meta:
        model = EvaluationCycle
        fields = ['employee','role','training','group','moment','start','end','notes',
            'weight_self','weight_manager','weight_peer','weight_trainer','minimum_coverage','minimum_peers']
        widgets = {'start': DATE(), 'end': DATE()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].queryset = RoleProfile.objects.filter(active=True)
        self.fields['training'].label_from_instance = lambda t: f'{t.employee.number} — {t.title} (#{t.pk})'
        self.fields['training'].queryset = Training.objects.select_related('employee').all()
        self.fields['employee'].help_text = 'O texto Função do cadastro permanece intacto; confirme a função de referência.'
        self.fields['minimum_coverage'].help_text = 'Aplica-se separadamente a cada tipo de avaliador com peso positivo.'
        self.fields['minimum_peers'].help_text = 'Nunca inferior a três. Os resultados só são divulgados após o fecho.'


class RaterForm(StyledModelForm):
    class Meta:
        model = CycleRater
        fields = ['user','kind','authorized']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = get_user_model().objects.filter(is_active=True).order_by('username')
        self.fields['authorized'].help_text = 'Confirme a identidade e o tipo do avaliador antes de autorizar.'


RaterSet = inlineformset_factory(EvaluationCycle, CycleRater, form=RaterForm,
    extra=3, can_delete=True, max_num=200, validate_max=True)


class TransitionForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo', max_length=1000, widget=forms.TextInput(attrs={'class': 'gf-field'}))
    confirm = forms.BooleanField(label='Confirmo as regras, autorizações e a conservação do histórico')
    action = forms.ChoiceField(choices=[('open','Abrir'), ('close','Fechar'), ('cancel','Cancelar')])


class ResponseForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo da gravação (sem notas ou comentários privados)', max_length=500,
        widget=forms.TextInput(attrs={'class': 'gf-field'}))

    def __init__(self, *args, rater, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['revision'] = rater.revision
        responses = {r.item_id: r for r in rater.responses.all()}
        self.rows = []
        for item in rater.cycle.items.all():
            previous = responses.get(item.pk)
            name = f'item_{item.pk}'
            self.fields[name] = forms.IntegerField(label='Nota (1–5)', required=False, min_value=1, max_value=5,
                initial=previous.score if previous else None,
                widget=forms.NumberInput(attrs={'class': 'gf-field','min':1,'max':5}))
            self.fields[name + '_comment'] = forms.CharField(label='Comentário privado', required=False, max_length=5000,
                initial=previous.comment if previous else '', widget=forms.Textarea(attrs={'class':'gf-field','rows':3}))
            self.rows.append((item, self[name], self[name + '_comment']))


class ActionForm(EditForm):
    class Meta:
        model = DevelopmentAction
        fields = ['course','action','responsible','due','state','review_date','review']
        widgets = {'due': DATE(), 'review_date': DATE()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['course'].queryset = Course.objects.filter(Q(active=True) | Q(pk=self.instance.course_id))


def managed_cycles(user):
    query = EvaluationCycle.objects.select_related('employee','role','owner')
    return query if user.is_superuser else query.filter(owner=user)


def get_cycle(user, pk):
    if not user.is_staff:
        raise PermissionDenied
    return get_object_or_404(managed_cycles(user), pk=pk)


def form_page(request, title, form, **context):
    return render(request, 'competencies/form.html', {'title':title,'form':form, **context})


def error_message(exc):
    return '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Registo duplicado. Reveja os campos.'


def conflict():
    return HttpResponse('O registo mudou ou está fechado. Reabra a página.', status=409)


@login_required
@staff_required
def index(request):
    query = request.GET.get('q','').strip()
    cycles = managed_cycles(request.user)
    if query:
        cycles = cycles.filter(Q(employee__name__icontains=query) | Q(employee__number__icontains=query))
    return render(request, 'competencies/index.html', {'page':Paginator(cycles,25).get_page(request.GET.get('page')), 'q':query})


@login_required
@staff_required
def catalog(request):
    return render(request, 'competencies/catalog.html', {'competencies':Competency.objects.all(), 'roles':RoleProfile.objects.all()})


@login_required
@staff_required
def catalog_edit(request, kind, pk=None):
    model, cls = (Competency, CompetencyForm) if kind == 'competency' else (RoleProfile, RoleForm)
    instance = get_object_or_404(model, pk=pk) if pk else model()
    before = model_to_dict(instance)
    data = request.POST if request.method == 'POST' else None
    form = cls(data, instance=instance, initial={'revision':instance.revision})
    rules = RuleSet(data, instance=instance, prefix='rules') if kind == 'role' else None
    if request.method == 'POST' and form.is_valid() and (rules is None or rules.is_valid()):
        try:
            with transaction.atomic():
                if pk and model.objects.filter(pk=pk, revision=form.cleaned_data['revision']).update(revision=F('revision')+1) != 1:
                    raise RevisionConflict('O catálogo mudou noutra janela.')
                if rules is not None:
                    before['rules'] = list(instance.rules.values()) if pk else []
                obj = form.save(commit=False)
                obj.revision += 1
                obj.save()
                if rules is not None:
                    rules.instance = obj
                    rules.save()
                record(request, 'COMPETENCY_CATALOG', f'{kind} #{obj.pk}; motivo: {form.cleaned_data["reason"]}; BEFORE {before}; AFTER {model_to_dict(obj)}')
                if rules is not None:
                    record(request, 'ROLE_RULES', f'Função #{obj.pk}; AFTER {list(obj.rules.values())}')
            return redirect('competency_catalog')
        except RevisionConflict:
            return conflict()
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, error_message(exc))
    return form_page(request, 'Competência' if kind == 'competency' else 'Função e regras RH', form, formset=rules)


@login_required
@staff_required
def cycle_edit(request, pk=None):
    cycle = get_cycle(request.user, pk) if pk else EvaluationCycle(owner=request.user)
    if cycle.state != 'draft':
        return conflict()
    before = model_to_dict(cycle)
    data = request.POST if request.method == 'POST' else None
    form = CycleForm(data, instance=cycle, initial={'revision':cycle.revision})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if pk and EvaluationCycle.objects.filter(pk=pk, revision=form.cleaned_data['revision'], state='draft').update(revision=F('revision')+1) != 1:
                return conflict()
            obj = form.save(commit=False)
            obj.revision += 1
            obj.save()
            record(request,'CYCLE_CONFIG',f'Ciclo #{obj.pk}; motivo: {form.cleaned_data["reason"]}; BEFORE {before}; AFTER {model_to_dict(obj)}')
        return redirect('cycle_detail', pk=obj.pk)
    return form_page(request, 'Preparar ciclo 360°', form, cycle=cycle if cycle.pk else None)


@login_required
@staff_required
def raters_edit(request, pk):
    cycle = get_cycle(request.user, pk)
    if cycle.state != 'draft':
        return conflict()
    data = request.POST if request.method == 'POST' else None
    form = forms.Form(data)
    form.fields['revision'] = forms.IntegerField(min_value=1, widget=forms.HiddenInput, initial=cycle.revision)
    formset = RaterSet(data, instance=cycle, prefix='raters')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        try:
            with transaction.atomic():
                lock_cycle(cycle, form.cleaned_data['revision'], 'draft')
                before = list(cycle.raters.values('id','user_id','kind','authorized'))
                formset.save()
                record(request, 'CYCLE_AUTHORIZATION', f'Ciclo #{pk}; BEFORE {before}; AFTER {list(cycle.raters.values("id","user_id","kind","authorized"))}')
            return redirect('cycle_detail', pk=pk)
        except RevisionConflict:
            return conflict()
        except IntegrityError:
            form.add_error(None, 'Cada conta só pode ser autorizada uma vez por ciclo.')
    return form_page(request, 'Avaliadores e autorizações', form, formset=formset, cycle=cycle)


@login_required
@staff_required
def cycle_detail(request, pk):
    cycle = get_cycle(request.user, pk)
    form = TransitionForm(request.POST if request.method == 'POST' else None, initial={'revision':cycle.revision})
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                if form.cleaned_data['action'] == 'open':
                    open_cycle(cycle, form.cleaned_data['revision'])
                else:
                    finish_cycle(cycle, form.cleaned_data['revision'], form.cleaned_data['action'] == 'cancel')
                record(request, 'CYCLE_STATE', f'Ciclo #{pk}; {form.cleaned_data["action"]}; motivo: {form.cleaned_data["reason"]}')
            return redirect('cycle_detail', pk=pk)
        except RevisionConflict:
            return conflict()
        except ValidationError as exc:
            form.add_error(None,error_message(exc))
    return render(request, 'competencies/cycle.html', {'cycle':cycle, 'form':form,
        'raters':cycle.raters.select_related('user'), 'results':cycle_results(cycle)})


@login_required
def my_ratings(request):
    rows = request.user.rating_assignments.filter(authorized=True).exclude(cycle__state='draft').select_related('cycle__employee')
    return render(request, 'competencies/my_ratings.html', {'assignments':rows})


@login_required
def respond(request, code):
    rater = get_object_or_404(CycleRater.objects.select_related('cycle__employee'), code=code, user=request.user, authorized=True)
    if rater.cycle.state == 'draft':
        raise PermissionDenied
    form = ResponseForm(request.POST if request.method == 'POST' else None, rater=rater)
    editable = rater.cycle.state == 'open' and not rater.submitted_at and rater.cycle.start <= timezone.localdate() <= rater.cycle.end
    if request.method == 'POST':
        if not editable:
            return conflict()
        if form.is_valid():
            submit = request.POST.get('action') == 'submit'
            if request.POST.get('action') not in ('save','submit'):
                form.add_error(None,'Acção inválida.')
            if submit and any(form.cleaned_data[f'item_{item.pk}'] is None for item, _, _ in form.rows):
                form.add_error(None,'Responda a todas as competências antes de entregar.')
            if not form.errors:
                try:
                    with transaction.atomic():
                        # Serialise with close/cancel and protect against obsolete browser windows.
                        lock_cycle(rater.cycle, rater.cycle.revision, 'open')
                        if CycleRater.objects.filter(pk=rater.pk, revision=form.cleaned_data['revision'], submitted_at__isnull=True).update(revision=F('revision')+1) != 1:
                            raise RevisionConflict('A resposta mudou noutra janela.')
                        before = list(rater.responses.values('item_id','score','comment'))
                        for item, _, _ in form.rows:
                            name = f'item_{item.pk}'
                            value = form.cleaned_data[name]
                            if value is None:
                                rater.responses.filter(item=item).delete()
                            else:
                                answer = rater.responses.filter(item=item).first() or CompetencyResponse(rater=rater,item=item)
                                answer.score, answer.comment = value, form.cleaned_data[name+'_comment']
                                answer.save()
                        if submit:
                            CycleRater.objects.filter(pk=rater.pk).update(submitted_at=timezone.now())
                        PrivateResponseRevision.objects.create(rater=rater, reason=form.cleaned_data['reason'],
                            payload={'before':before,'after':list(rater.responses.values('item_id','score','comment'))})
                        # Do not copy confidential scores/comments into the broadly visible audit log.
                        record(request,'PRIVATE_360_RESPONSE', f'Avaliador {rater.code}; ciclo {rater.cycle_id}; '+('entrega' if submit else 'rascunho'))
                    return redirect('my_ratings')
                except RevisionConflict:
                    return conflict()
    response = render(request, 'competencies/respond.html', {'rater':rater,'form':form,'editable':editable,
        'responses':rater.responses.select_related('item')})
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@staff_required
def development(request):
    actions = DevelopmentAction.objects.filter(item__cycle__in=managed_cycles(request.user)).select_related('item__cycle__employee','course')
    return render(request, 'competencies/development.html', {'actions':actions})


@login_required
@staff_required
def action_edit(request, item_pk, pk=None):
    item = get_object_or_404(CycleCompetency.objects.select_related('cycle'), pk=item_pk, cycle__in=managed_cycles(request.user))
    action = get_object_or_404(DevelopmentAction, pk=pk, item=item) if pk else DevelopmentAction(item=item)
    before = model_to_dict(action)
    form = ActionForm(request.POST if request.method == 'POST' else None, instance=action, initial={'revision':action.revision})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if pk and DevelopmentAction.objects.filter(pk=pk, revision=form.cleaned_data['revision']).update(revision=F('revision')+1) != 1:
                return conflict()
            obj = form.save(commit=False)
            obj.revision += 1
            obj.save()
            record(request,'DEVELOPMENT_ACTION',f'Acção #{obj.pk}; motivo: {form.cleaned_data["reason"]}; BEFORE {before}; AFTER {model_to_dict(obj)}')
        return redirect('development_index')
    return form_page(request, f'Plano de desenvolvimento — {item.name}', form, cycle=item.cycle)

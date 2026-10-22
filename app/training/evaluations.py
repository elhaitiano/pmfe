"""Staff-only recording and marking; no participant portal or automatic approval."""
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.forms import inlineformset_factory, model_to_dict
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import StyledModelForm
from .models import Employee, Training, TrainingEvaluation, ReactionEvaluation, Exam, ExamQuestion, ExamAttempt, ExamAnswer
from .views import staff_required, record

EVIDENCE = ['date', 'evaluator', 'source', 'validation']
DATE_WIDGET = {'date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})}


class RevisionForm(StyledModelForm):
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    correction_reason = forms.CharField(label='Motivo da gravação / correcção', max_length=1000)


class EvaluationForm(RevisionForm):
    grade = forms.DecimalField(label='Nota final /20', required=False, min_value=0, max_value=20,
                               max_digits=4, decimal_places=2)
    result = forms.ChoiceField(label='Resultado final', choices=Training._meta.get_field('result').choices)

    class Meta:
        model = TrainingEvaluation
        fields = ['logistics', 'comments'] + EVIDENCE
        widgets = DATE_WIDGET


class ReactionForm(RevisionForm):
    class Meta:
        model = ReactionEvaluation
        fields = ['organization', 'content', 'trainer', 'usefulness', 'comments'] + EVIDENCE
        widgets = DATE_WIDGET


class ExamForm(RevisionForm):
    class Meta:
        model = Exam
        fields = ['title', 'kind', 'pass_percent'] + EVIDENCE
        widgets = DATE_WIDGET


class QuestionForm(StyledModelForm):
    class Meta:
        model = ExamQuestion
        fields = ['number', 'prompt', 'expected_answer', 'points', 'reference']
        widgets = {name: forms.Textarea(attrs={'rows': 3}) for name in ['prompt', 'expected_answer']}


QuestionSet = inlineformset_factory(Exam, ExamQuestion, form=QuestionForm,
    extra=3, can_delete=True, max_num=100, validate_max=True)


class AttemptForm(RevisionForm):
    class Meta:
        model = ExamAttempt
        fields = EVIDENCE
        widgets = DATE_WIDGET

    def __init__(self, *args, answers, **kwargs):
        super().__init__(*args, **kwargs)
        self.answer_rows = []
        for answer in answers:
            prefix = f'q{answer.question_id}'
            self.fields[prefix + '_response'] = forms.CharField(label='Resposta', required=False,
                widget=forms.Textarea(attrs={'rows': 3, 'class': 'gf-field'}), initial=answer.response)
            self.fields[prefix + '_awarded'] = forms.DecimalField(label='Pontos obtidos', required=False,
                min_value=0, max_value=answer.question.points, max_digits=7, decimal_places=2,
                initial=answer.awarded, widget=forms.NumberInput(attrs={'class': 'gf-field', 'step': '0.01'}))
            self.fields[prefix + '_comment'] = forms.CharField(label='Observação da correcção', required=False,
                initial=answer.comment, widget=forms.Textarea(attrs={'rows': 2, 'class': 'gf-field'}))
            self.answer_rows.append((answer, [self[prefix + suffix] for suffix in ['_response', '_awarded', '_comment']]))

    @property
    def metadata(self):
        return [self[name] for name in EVIDENCE + ['correction_reason']]


class RetakeForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo da autorização (obrigatório após a primeira tentativa)',
        required=False, max_length=1000, widget=forms.TextInput(attrs={'class': 'gf-field'}))


def conflict():
    return HttpResponse('O registo mudou ou está fechado. Reabra a página antes de guardar.', status=409)


def audited(request, action, obj, before, reason):
    record(request, action, f'{obj.__class__.__name__} #{obj.pk}; motivo: {reason}; '
           f'BEFORE {before}; AFTER {model_to_dict(obj)}')


@login_required
@staff_required
def index(request):
    rows = Training.objects.select_related('employee').all()
    query = request.GET.get('q', '').strip()
    if query:
        rows = rows.filter(Q(title__icontains=query) | Q(employee__name__icontains=query) |
                           Q(employee__number__icontains=query))
    people = Employee.objects.all()
    if query:
        people = people.filter(Q(name__icontains=query) | Q(number__icontains=query))
    return render(request, 'evaluation_index.html', {'people': people[:30], 'page': Paginator(rows, 30).get_page(request.GET.get('page')), 'q': query})


@login_required
@staff_required
def detail(request, pk):
    training = get_object_or_404(Training.objects.select_related('employee'), pk=pk)
    return render(request, 'evaluation_detail.html', {'training': training, 'employee': training.employee,
        'evaluation': TrainingEvaluation.objects.filter(training=training).first(),
        'reaction': ReactionEvaluation.objects.filter(training=training).first(),
        'exams': training.exams.prefetch_related('attempts', 'questions')})


@login_required
@staff_required
def record_edit(request, pk, kind):
    training = get_object_or_404(Training, pk=pk)
    model, form_class = (TrainingEvaluation, EvaluationForm) if kind == 'learning' else (ReactionEvaluation, ReactionForm)
    instance = model.objects.filter(training=training).first() or model(training=training)
    before = model_to_dict(instance)
    initial = {'revision': training.version, 'grade': training.grade, 'result': training.result}
    form = form_class(request.POST if request.method == 'POST' else None, instance=instance, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if kind == 'learning':
            before.update(grade=str(training.grade), result=training.result)
            training.grade, training.result = form.cleaned_data['grade'], form.cleaned_data['result']
            try:
                training.full_clean()
            except ValidationError as exc:
                form.add_error(None, '; '.join(exc.messages))
        if not form.errors:
            with transaction.atomic():
                updates = {'version': F('version') + 1}
                if kind == 'learning':
                    updates.update(grade=training.grade, result=training.result)
                if not Training.objects.filter(pk=pk, version=form.cleaned_data['revision']).update(**updates):
                    return conflict()
                obj = form.save(commit=False)
                if kind == 'learning':
                    obj.validated_grade = training.grade if obj.validation == 'Validado' else None
                    obj.validated_result = training.result if obj.validation == 'Validado' else ''
                obj.revision += 1
                obj.save()
                audited(request, 'UPDATE_EVALUATION', obj, before, form.cleaned_data['correction_reason'])
                if kind == 'learning':
                    record(request, 'UPDATE_FINAL_RESULT', f'Participação {pk}; BEFORE {before}; '
                        f'AFTER nota={training.grade}, resultado={training.result}; motivo={form.cleaned_data["correction_reason"]}')
            return redirect('evaluation_detail', pk=pk)
    return render(request, 'evaluation_form.html', {'form': form, 'training': training,
        'title': 'Avaliação da formação' if kind == 'learning' else 'Reacção do participante'})


@login_required
@staff_required
def exam_edit(request, pk, exam_pk=None):
    training = get_object_or_404(Training, pk=pk)
    exam = get_object_or_404(Exam, pk=exam_pk, training=training) if exam_pk else Exam(training=training)
    if exam.pk and exam.attempts.exists():
        return conflict()
    before = {'exam': model_to_dict(exam), 'questions': list(exam.questions.values()) if exam.pk else []}
    data = request.POST if request.method == 'POST' else None
    form = ExamForm(data, instance=exam, initial={'revision': exam.revision})
    questions = QuestionSet(data, instance=exam, prefix='questions')
    if request.method == 'POST' and form.is_valid() and questions.is_valid():
        try:
            with transaction.atomic():
                if exam.pk and not Exam.objects.filter(pk=exam.pk, revision=form.cleaned_data['revision']).update(revision=F('revision') + 1):
                    return conflict()
                if exam.pk and exam.attempts.exists():
                    return conflict()
                obj = form.save(commit=False)
                obj.revision += 1
                obj.save()
                questions.instance = obj
                questions.save()
                audited(request, 'UPDATE_EXAM', obj, before, form.cleaned_data['correction_reason'])
                record(request, 'EXAM_QUESTIONS', f'Prova {obj.pk}; AFTER {list(obj.questions.values())}')
            return redirect('evaluation_detail', pk=pk)
        except ValidationError as exc:
            form.add_error(None, '; '.join(exc.messages))
    return render(request, 'evaluation_form.html', {'form': form, 'questions': questions,
        'training': training, 'title': 'Prova e critérios de correcção'})


@login_required
@staff_required
def attempt_create(request, pk):
    exam = get_object_or_404(Exam.objects.select_related('training'), pk=pk)
    form = RetakeForm(request.POST if request.method == 'POST' else None, initial={'revision': exam.revision})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if not Exam.objects.filter(pk=pk, revision=form.cleaned_data['revision']).update(revision=F('revision') + 1):
                return conflict()
            previous = exam.attempts.order_by('-number').first()
            if previous and not previous.finalized:
                return HttpResponse('Conclua a tentativa aberta antes de autorizar outra.', status=400)
            if previous and not form.cleaned_data['reason'].strip():
                return HttpResponse('Indique o motivo da autorização da nova tentativa.', status=400)
            questions = list(exam.questions.all())
            if not questions:
                return HttpResponse('A prova precisa de pelo menos uma questão.', status=400)
            attempt = ExamAttempt(exam=exam, number=previous.number + 1 if previous else 1,
                authorization_reason=form.cleaned_data['reason'], authorized_by=request.user.username)
            attempt.full_clean()
            attempt.save()
            ExamAnswer.objects.bulk_create([ExamAnswer(attempt=attempt, question=q) for q in questions])
            audited(request, 'AUTHORIZE_ATTEMPT', attempt, {}, form.cleaned_data['reason'] or 'Primeira tentativa')
        return redirect('attempt_edit', pk=attempt.pk)
    return render(request, 'evaluation_form.html', {'form': form, 'training': exam.training,
        'title': f'Autorizar tentativa — {exam.title}'})


@login_required
@staff_required
def attempt_edit(request, pk):
    attempt = get_object_or_404(ExamAttempt.objects.select_related('exam__training'), pk=pk)
    answers = list(attempt.answers.select_related('question'))
    before = {'attempt': model_to_dict(attempt), 'answers': [model_to_dict(a) for a in answers]}
    form = AttemptForm(request.POST if request.method == 'POST' else None, instance=attempt,
        answers=answers, initial={'revision': attempt.revision})
    if request.method == 'POST':
        if attempt.finalized:
            return conflict()
        if form.is_valid():
            finalize = request.POST.get('action') == 'finalize'
            if request.POST.get('action') not in ('save', 'finalize'):
                form.add_error(None, 'Acção inválida.')
            for answer in answers:
                prefix = f'q{answer.question_id}'
                answer.response = form.cleaned_data[prefix + '_response']
                answer.awarded = form.cleaned_data[prefix + '_awarded']
                answer.comment = form.cleaned_data[prefix + '_comment']
            if finalize and (not answers or any(a.awarded is None for a in answers)):
                form.add_error(None, 'Classifique todas as questões; uma pontuação ausente não equivale a zero.')
            if finalize and not (attempt.date and attempt.evaluator.strip() and attempt.source.strip()):
                form.add_error(None, 'A conclusão exige data, responsável e fonte documental.')
            if not form.errors:
                with transaction.atomic():
                    if not ExamAttempt.objects.filter(pk=pk, revision=form.cleaned_data['revision'], finalized=False).update(revision=F('revision') + 1):
                        return conflict()
                    for answer in answers:
                        answer.full_clean()
                        answer.save()
                    obj = form.save(commit=False)
                    obj.revision += 1
                    if finalize:
                        obj.total = sum((a.awarded for a in answers), Decimal(0))
                        obj.maximum = sum((a.question.points for a in answers), Decimal(0))
                        obj.threshold = attempt.exam.pass_percent
                        obj.finalized = True
                        obj.finalized_at = timezone.now()
                    obj.save()
                    audited(request, 'MARK_ATTEMPT', obj, before, form.cleaned_data['correction_reason'])
                    record(request, 'ATTEMPT_ANSWERS', f'Tentativa {pk}; AFTER {[model_to_dict(a) for a in answers]}')
                messages.success(request, 'Tentativa guardada. O resultado não altera automaticamente a nota final da participação.')
                return redirect('attempt_edit', pk=pk)
    return render(request, 'evaluation_attempt.html', {'attempt': attempt, 'form': form,
        'answers': answers, 'training': attempt.exam.training})

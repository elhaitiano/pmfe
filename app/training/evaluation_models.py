"""Learning records: participation is the single source of employee identity."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class EvidenceRecord(models.Model):
    source = models.TextField('Fonte documental / referência', blank=True)
    validation = models.CharField('Estado de validação', max_length=20, default='Por confirmar',
        choices=[(x, x) for x in ['Por confirmar', 'Validado', 'Rever']])
    evaluator = models.CharField('Responsável pela avaliação', max_length=150, blank=True)
    date = models.DateField('Data da avaliação', null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if self.date and self.date > timezone.localdate():
            raise ValidationError('A data da avaliação não pode estar no futuro.')
        if self.validation == 'Validado' and not (self.source.strip() and self.evaluator.strip() and self.date):
            raise ValidationError('A validação exige fonte documental, responsável e data.')


class TrainingEvaluation(EvidenceRecord):
    training = models.OneToOneField('Training', on_delete=models.PROTECT, related_name='evaluation')
    logistics = models.DecimalField('Avaliação logística /5', max_digits=3, decimal_places=2,
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    comments = models.TextField('Observações', blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(logistics__isnull=True) |
            models.Q(logistics__gte=1, logistics__lte=5), name='evaluation_logistics_range')]


class ReactionEvaluation(EvidenceRecord):
    training = models.OneToOneField('Training', on_delete=models.PROTECT, related_name='reaction')
    organization = models.PositiveSmallIntegerField('Organização /5', null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)])
    content = models.PositiveSmallIntegerField('Conteúdo /5', null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)])
    trainer = models.PositiveSmallIntegerField('Formador /5', null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)])
    usefulness = models.PositiveSmallIntegerField('Utilidade /5', null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)])
    comments = models.TextField('Comentários do participante', blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(**{f'{field}__isnull': True}) |
            models.Q(**{f'{field}__gte': 1, f'{field}__lte': 5}), name=f'reaction_{field}_range')
            for field in ['organization', 'content', 'trainer', 'usefulness']]


class Exam(EvidenceRecord):
    training = models.ForeignKey('Training', on_delete=models.PROTECT, related_name='exams')
    title = models.CharField('Título da prova', max_length=200)
    kind = models.CharField('Tipo de prova', max_length=30,
        choices=[(x, x) for x in ['Diagnóstica', 'Intermédia', 'Final']], default='Final')
    pass_percent = models.DecimalField('Critério de aprovação (%)', max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)], default=50)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(pass_percent__gte=0, pass_percent__lte=100),
            name='exam_pass_range')]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, attempts__isnull=False).exists():
            raise ValidationError('Uma prova com tentativas é conservada. Crie outra prova para novos critérios.')
        return super().save(*args, **kwargs)

    @property
    def maximum(self):
        return sum((q.points for q in self.questions.all()), Decimal('0'))


class ExamQuestion(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name='questions')
    number = models.PositiveIntegerField('Número', validators=[MinValueValidator(1)])
    prompt = models.TextField('Enunciado')
    expected_answer = models.TextField('Resposta esperada / critérios de correcção')
    points = models.DecimalField('Cotação máxima', max_digits=7, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))])
    reference = models.CharField('Referência do manual', max_length=250, blank=True)

    class Meta:
        ordering = ['number']
        constraints = [models.UniqueConstraint(fields=['exam', 'number'], name='exam_question_number'),
            models.CheckConstraint(condition=models.Q(points__gt=0), name='question_positive_points')]


    def save(self, *args, **kwargs):
        previous_exam = type(self).objects.filter(pk=self.pk).values_list('exam_id', flat=True).first() if self.pk else None
        if ExamAttempt.objects.filter(exam_id__in=[self.exam_id, previous_exam]).exists():
            raise ValidationError('Não é possível alterar questões de uma prova com tentativas.')
        return super().save(*args, **kwargs)


class ExamAttempt(EvidenceRecord):
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name='attempts')
    number = models.PositiveIntegerField()
    authorization_reason = models.TextField('Motivo da nova tentativa', blank=True)
    authorized_by = models.CharField(max_length=150, blank=True)
    finalized = models.BooleanField(default=False)
    finalized_at = models.DateTimeField(null=True, blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    maximum = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    threshold = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ['number']
        constraints = [models.UniqueConstraint(fields=['exam', 'number'], name='exam_attempt_number')]

    def clean(self):
        super().clean()
        if self.number and self.number > 1 and not (self.authorization_reason.strip() and self.authorized_by):
            raise ValidationError('Uma nova tentativa exige autorização e motivo.')

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if previous.finalized or previous.exam_id != self.exam_id or previous.number != self.number:
                raise ValidationError('A identidade e os resultados de uma tentativa concluída são conservados.')
        return super().save(*args, **kwargs)

    @property
    def percent(self):
        if self.total is None or not self.maximum:
            return None
        return self.total * Decimal(100) / self.maximum

    @property
    def grade20(self):
        return None if self.percent is None else self.percent / Decimal(5)

    @property
    def result(self):
        if not self.finalized or self.percent is None:
            return 'Pendente'
        return 'Aprovado' if self.percent >= self.threshold else 'Reprovado'


class ExamAnswer(models.Model):
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.PROTECT, related_name='answers')
    question = models.ForeignKey(ExamQuestion, on_delete=models.PROTECT, related_name='answers')
    response = models.TextField('Resposta do participante', blank=True)
    awarded = models.DecimalField('Pontos obtidos', max_digits=7, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)])
    comment = models.TextField('Observações da correcção', blank=True)

    class Meta:
        ordering = ['question__number']
        constraints = [models.UniqueConstraint(fields=['attempt', 'question'], name='attempt_question_once'),
            models.CheckConstraint(condition=models.Q(awarded__isnull=True) | models.Q(awarded__gte=0),
                name='answer_nonnegative')]

    def clean(self):
        super().clean()
        if self.question_id and self.attempt_id:
            if self.question.exam_id != self.attempt.exam_id:
                raise ValidationError('A questão pertence a outra prova.')
            if self.awarded is not None and self.awarded > self.question.points:
                raise ValidationError('A pontuação excede a cotação da questão.')


    def save(self, *args, **kwargs):
        if ExamAttempt.objects.filter(pk=self.attempt_id, finalized=True).exists():
            raise ValidationError('Uma tentativa concluída não aceita alterações de respostas.')
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if previous.attempt_id != self.attempt_id or previous.question_id != self.question_id:
                raise ValidationError('A ligação entre resposta, questão e tentativa é permanente.')
        self.full_clean()
        return super().save(*args, **kwargs)

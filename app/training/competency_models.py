"""Competency catalogues and immutable, explicitly authorised 360 cycles."""
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

RATER_TYPES = [('self', 'Autoavaliação'), ('manager', 'Superior'), ('peer', 'Colegas'), ('trainer', 'Formador')]


class Competency(models.Model):
    identity_key = models.CharField(max_length=600, unique=True, editable=False)
    name = models.CharField('Competência', max_length=200)
    dimension = models.CharField('Dimensão', max_length=150, blank=True)
    description = models.TextField('Descrição', blank=True)
    active = models.BooleanField('Activa', default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(Lower('name'), name='competency_name_unique')]

    def save(self, *args, **kwargs):
        self.name = ' '.join(self.name.split())
        self.identity_key = self.name.casefold()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class RoleProfile(models.Model):
    identity_key = models.CharField(max_length=600, unique=True, editable=False)
    title = models.CharField('Função do catálogo', max_length=200)
    description = models.TextField('Descrição / correspondência com o cadastro', blank=True)
    active = models.BooleanField('Activa', default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['title']
        constraints = [models.UniqueConstraint(Lower('title'), name='role_title_unique')]

    def save(self, *args, **kwargs):
        self.title = ' '.join(self.title.split())
        self.identity_key = self.title.casefold()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class RoleCompetency(models.Model):
    role = models.ForeignKey(RoleProfile, on_delete=models.PROTECT, related_name='rules')
    competency = models.ForeignKey(Competency, on_delete=models.PROTECT, verbose_name='Competência')
    expected = models.PositiveSmallIntegerField('Nível esperado (1–5)', default=3,
        validators=[MinValueValidator(1), MaxValueValidator(5)])
    weight = models.DecimalField('Peso relativo da competência', max_digits=7, decimal_places=2,
        default=1, validators=[MinValueValidator(Decimal('0.01'))])
    validated = models.BooleanField('Regra validada pelos RH', default=False)
    validated_by = models.CharField('Responsável RH', max_length=150, blank=True)
    validated_on = models.DateField('Data da validação RH', null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['role', 'competency'], name='role_competency_unique'),
            models.CheckConstraint(condition=models.Q(expected__gte=1, expected__lte=5), name='role_expected_range'),
            models.CheckConstraint(condition=models.Q(weight__gt=0), name='role_weight_positive')]

    def clean(self):
        if self.validated and not (self.validated_by.strip() and self.validated_on):
            raise ValidationError('A validação RH exige responsável e data.')
        if self.validated_on and self.validated_on > timezone.localdate():
            raise ValidationError('A validação RH não pode ter data futura.')


class EvaluationCycle(models.Model):
    employee = models.ForeignKey('Employee', on_delete=models.PROTECT, related_name='evaluation_cycles', verbose_name='Trabalhador')
    role = models.ForeignKey(RoleProfile, on_delete=models.PROTECT, verbose_name='Função de referência')
    training = models.ForeignKey('Training', on_delete=models.PROTECT, related_name='evaluation_cycles',
        null=True, blank=True, verbose_name='Participação (opcional)')
    group = models.ForeignKey('TrainingGroup', on_delete=models.PROTECT, related_name='evaluation_cycles',
        null=True, blank=True, verbose_name='Turma (opcional)')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='managed_cycles')
    moment = models.CharField('Momento', max_length=30,
        choices=[(x,x) for x in ['Antes da formação', 'Após a formação', 'Acompanhamento']])
    start = models.DateField('Início')
    end = models.DateField('Fim')
    notes = models.TextField('Observações', blank=True)
    weight_self = models.DecimalField('Peso autoavaliação (%)', max_digits=5, decimal_places=2, default=10,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    weight_manager = models.DecimalField('Peso superior (%)', max_digits=5, decimal_places=2, default=40,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    weight_peer = models.DecimalField('Peso colegas (%)', max_digits=5, decimal_places=2, default=25,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    weight_trainer = models.DecimalField('Peso formador (%)', max_digits=5, decimal_places=2, default=25,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    minimum_coverage = models.PositiveSmallIntegerField('Cobertura mínima por tipo (%)', default=100,
        validators=[MinValueValidator(1), MaxValueValidator(100)])
    minimum_peers = models.PositiveSmallIntegerField('Mínimo de colegas para divulgação', default=3,
        validators=[MinValueValidator(3), MaxValueValidator(100)])
    state = models.CharField(max_length=12, default='draft', choices=[('draft','Preparação'),
        ('open','Aberto'), ('closed','Fechado'), ('cancelled','Cancelado')])
    revision = models.PositiveIntegerField(default=1)
    config = models.JSONField(default=dict, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-start','-pk']
        constraints = [models.CheckConstraint(condition=models.Q(end__gte=models.F('start')), name='cycle_dates'),
            models.CheckConstraint(condition=models.Q(minimum_peers__gte=3), name='cycle_peer_floor'),
            models.CheckConstraint(condition=models.Q(minimum_coverage__gte=1, minimum_coverage__lte=100), name='cycle_coverage_range')]

    def clean(self):
        if self.start and self.end and self.end < self.start:
            raise ValidationError('O fim do ciclo não pode anteceder o início.')
        weights = [getattr(self, 'weight_' + kind) for kind, _ in RATER_TYPES]
        if all(x is not None for x in weights) and sum(weights) != 100:
            raise ValidationError('Os pesos dos tipos de avaliador devem somar exactamente 100%.')
        if self.training_id and self.employee_id and self.training.employee_id != self.employee_id:
            raise ValidationError('A participação deve pertencer ao trabalhador do ciclo.')
        if self.group_id and self.training_id and self.training.group_id != self.group_id:
            raise ValidationError('A turma deve corresponder à participação.')
        if self.group_id and not self.training_id and self.employee_id:
            if not self.group.enrollments.filter(employee_id=self.employee_id, state='Activa').exists():
                raise ValidationError('Seleccione uma participação ou uma turma com inscrição activa deste trabalhador.')

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(state='draft').exists():
            raise ValidationError('A configuração de um ciclo iniciado é conservada. Crie um novo ciclo.')
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.employee} — {self.moment} (#{self.pk})'


class CycleCompetency(models.Model):
    cycle = models.ForeignKey(EvaluationCycle, on_delete=models.PROTECT, related_name='items')
    competency = models.ForeignKey(Competency, on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    dimension = models.CharField(max_length=150, blank=True)
    description = models.TextField(blank=True)
    expected = models.PositiveSmallIntegerField()
    weight = models.DecimalField(max_digits=7, decimal_places=2)

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(fields=['cycle','competency'], name='cycle_competency_unique')]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.pk or not EvaluationCycle.objects.filter(pk=self.cycle_id, state='draft').exists():
            raise ValidationError('As competências do ciclo são uma cópia histórica imutável.')
        return super().save(*args, **kwargs)


class CycleRater(models.Model):
    code = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    cycle = models.ForeignKey(EvaluationCycle, on_delete=models.PROTECT, related_name='raters')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='rating_assignments', verbose_name='Conta do avaliador')
    kind = models.CharField('Tipo', max_length=10, choices=RATER_TYPES)
    authorized = models.BooleanField('Autorizado pelo responsável', default=False)
    invited_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cycle','user'], name='cycle_user_once')]

    def save(self, *args, **kwargs):
        if not EvaluationCycle.objects.filter(pk=self.cycle_id, state='draft').exists():
            raise ValidationError('Os avaliadores ficam fixos ao abrir o ciclo.')
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if previous.cycle_id != self.cycle_id:
                raise ValidationError('Não é possível transferir um avaliador para outro ciclo.')
        return super().save(*args, **kwargs)


class CompetencyResponse(models.Model):
    rater = models.ForeignKey(CycleRater, on_delete=models.PROTECT, related_name='responses')
    item = models.ForeignKey(CycleCompetency, on_delete=models.PROTECT, related_name='responses')
    score = models.PositiveSmallIntegerField('Nota (1–5)', validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField('Comentário privado', blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['rater','item'], name='rater_item_once'),
            models.CheckConstraint(condition=models.Q(score__gte=1, score__lte=5), name='competency_score_range')]

    def clean(self):
        if self.rater_id and self.item_id and self.rater.cycle_id != self.item.cycle_id:
            raise ValidationError('A competência pertence a outro ciclo.')

    def save(self, *args, **kwargs):
        current = CycleRater.objects.select_related('cycle').get(pk=self.rater_id)
        if current.submitted_at or current.cycle.state != 'open' or not current.authorized:
            raise ValidationError('As respostas entregues ou de um ciclo fechado são conservadas.')
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if previous.rater_id != self.rater_id or previous.item_id != self.item_id:
                raise ValidationError('A identidade de uma resposta é permanente.')
        self.full_clean()
        return super().save(*args, **kwargs)


class DevelopmentAction(models.Model):
    item = models.ForeignKey(CycleCompetency, on_delete=models.PROTECT, related_name='actions', verbose_name='Lacuna de competência')
    course = models.ForeignKey('Course', on_delete=models.PROTECT, related_name='development_actions',
        null=True, blank=True, verbose_name='Formação recomendada')
    action = models.TextField('Acção de desenvolvimento')
    responsible = models.CharField('Responsável pela execução', max_length=150)
    due = models.DateField('Prazo')
    state = models.CharField('Estado', max_length=20, default='Planeada',
        choices=[(x,x) for x in ['Planeada','Em curso','Concluída','Cancelada']])
    review_date = models.DateField('Data da revisão', null=True, blank=True)
    review = models.TextField('Revisão / evidência do resultado', blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due','pk']

    def clean(self):
        if self.review_date and self.review_date > timezone.localdate():
            raise ValidationError('A revisão não pode ter data futura.')
        if self.state in ('Concluída','Cancelada') and not (self.review_date and self.review.strip()):
            raise ValidationError('Concluir ou cancelar exige data e comentário de revisão.')
        if self.item_id:
            from .competency_services import cycle_results
            result = cycle_results(self.item.cycle)
            item = next((x for x in result['items'] if x['id'] == self.item_id), None)
            if not item or item['gap'] is None or item['gap'] <= 0:
                raise ValidationError('A acção exige uma lacuna de um ciclo fechado com cobertura suficiente.')

    @property
    def overdue(self):
        return self.state in ('Planeada','Em curso') and self.due < timezone.localdate()


class PrivateResponseRevision(models.Model):
    """Confidential correction history; never copied into the general audit feed."""
    rater = models.ForeignKey(CycleRater, on_delete=models.PROTECT, related_name='private_history')
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=500)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ['-created_at','-pk']

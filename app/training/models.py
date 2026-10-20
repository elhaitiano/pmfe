from decimal import Decimal
import uuid
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class Employee(models.Model):
    number = models.CharField('N.º Mecanográfico', max_length=40, unique=True)
    name = models.CharField('Nome completo', max_length=200)
    direction = models.CharField('Direcção / Gabinete', max_length=200, blank=True)
    category = models.CharField('Categoria', max_length=200, blank=True)
    function = models.CharField('Função exercida', max_length=200, blank=True)
    area = models.CharField('Área alocada', max_length=200, blank=True)
    email = models.EmailField('E-mail', blank=True)
    phone = models.CharField('Telefone', max_length=80, blank=True)
    birth_date = models.DateField('Data de nascimento', null=True, blank=True)
    admission_date = models.DateField('Data de admissão', null=True, blank=True)
    # Retains unmapped source fields for subsequent migration stages.
    source_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['name', 'number']

    def __str__(self):
        return f'{self.number} — {self.name}'

    @property
    def validation_status(self):
        return self.source_data.get('Validação Cadastro EPAL', 'Por confirmar')


class Training(models.Model):
    group = models.ForeignKey('TrainingGroup', on_delete=models.PROTECT, null=True, blank=True, related_name='trainings')
    attendance_confirmed = models.BooleanField('Presenças verificadas (incluindo zero)', default=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='trainings')
    title = models.CharField('Nome da formação', max_length=1000)
    area = models.CharField('Área / Categoria', max_length=200, blank=True)
    trainer = models.CharField('Formador(a)', max_length=200, blank=True)
    provider = models.CharField('Entidade formadora', max_length=200, blank=True)
    location = models.CharField('Local da formação', max_length=200, blank=True)
    start = models.DateField('Data de início')
    end = models.DateField('Data de fim')
    days = models.PositiveIntegerField('Duração (dias)', validators=[MinValueValidator(1)])
    hours_per_day = models.DecimalField('Horas por dia', max_digits=4, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.5')), MaxValueValidator(24)])
    attendance = models.PositiveIntegerField('N.º de presenças')
    grade = models.DecimalField('Média final /20', max_digits=4, decimal_places=2,
        null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(20)])
    certified = models.BooleanField('Certificado entregue', default=None, null=True, blank=True)
    result = models.CharField('Resultado final', max_length=30, default='Pendente', choices=[(x,x) for x in ['Pendente','Aprovado','Reprovado','Não avaliado']])
    certificate_reference = models.CharField('Nº/Ref. Certificado', max_length=150, blank=True)
    certificate_delivery_date = models.DateField('Data de entrega do certificado', null=True, blank=True)
    observations = models.TextField('Observações', blank=True)
    status = models.CharField('Situação', max_length=30, default='Concluído', choices=[
        ('Concluído', 'Concluído'), ('Em Curso', 'Em Curso'), ('Planeado', 'Planeado'), ('Desistência','Desistência'), ('Por confirmar','Por confirmar')])
    source_key = models.CharField(max_length=80, unique=True, null=True, blank=True)
    source_data = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['-start', '-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(days__gte=1), name='training_positive_days'),
            models.CheckConstraint(condition=models.Q(attendance__lte=models.F('days')), name='training_attendance'),
            models.CheckConstraint(condition=models.Q(end__gte=models.F('start')), name='training_dates'),
            models.CheckConstraint(condition=models.Q(hours_per_day__gte=Decimal('0.5'), hours_per_day__lte=24), name='training_hours'),
            models.CheckConstraint(condition=models.Q(grade__isnull=True) | models.Q(grade__gte=0, grade__lte=20), name='training_grade'),
        ]

    def clean(self):
        errors = {}
        if self.certified is True and not self.certificate_delivery_date:
            errors['certificate_delivery_date'] = 'Indique a data efectiva da entrega.'
        if self.certificate_delivery_date and self.certified is not True:
            errors['certified'] = 'Uma data de entrega exige confirmação da entrega.'
        if self.certificate_delivery_date:
            if self.certificate_delivery_date > timezone.localdate():
                errors['certificate_delivery_date'] = 'A entrega confirmada não pode ter uma data futura.'
            elif self.end and self.certificate_delivery_date < self.end:
                errors['certificate_delivery_date'] = 'A entrega não pode anteceder o fim da formação.'
        if self.certified is True and self.status != 'Concluído':
            errors['certified'] = 'Finalize a formação antes de confirmar a entrega.'
        if self.grade is not None and self.status != 'Concluído':
            errors['grade'] = 'A nota final só pode ser registada numa formação concluída.'
        if self.result in ('Aprovado', 'Reprovado') and self.status != 'Concluído':
            errors['result'] = 'O resultado final exige uma formação concluída.'
        if self.status == 'Planeado' and self.attendance:
            errors['attendance'] = 'Uma formação não iniciada não pode ter presenças registadas.'
        if self.start and self.end and self.end < self.start:
            errors['end'] = 'O fim não pode ser anterior ao início.'
        if self.status == 'Concluído':
            for field in ['start', 'end']:
                value = getattr(self, field)
                if value and value > timezone.localdate():
                    errors[field] = 'Uma formação concluída não pode ter datas futuras.'
        if self.attendance is not None and self.days is not None and self.attendance > self.days:
            errors['attendance'] = 'As presenças não podem ultrapassar a duração em dias.'
        if errors:
            raise ValidationError(errors)

    @property
    def completed_hours(self):
        return self.attendance * self.hours_per_day

    @property
    def planned_hours(self):
        return self.days * self.hours_per_day

    @property
    def absences(self):
        return self.days - self.attendance if self.status in ('Concluído', 'Desistência') else None

    @property
    def certificate_label(self):
        return 'Sim' if self.certified is True else 'Não' if self.certified is False else 'Por confirmar'

    @property
    def attendance_percent(self):
        if not self.days or self.status == 'Planeado':
            return None
        from decimal import ROUND_HALF_UP
        return (Decimal(self.attendance) * 100 / self.days).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)


class TrainingDocumentState(models.Model):
    """Administrative supplement: never rewrites an imported Training row."""
    training = models.OneToOneField(Training, on_delete=models.PROTECT, related_name='document_state')
    issued = models.BooleanField('Emissão confirmada', null=True, blank=True, default=None)
    learner_delivered = models.BooleanField('Entregue ao formando', null=True, blank=True, default=None)
    learner_date = models.DateField('Data de entrega ao formando', null=True, blank=True)
    learner_responsible = models.CharField('Responsável pela entrega ao formando', max_length=150, blank=True)
    hr_delivered = models.BooleanField('Exemplar entregue aos RH EPAL', null=True, blank=True, default=None)
    hr_date = models.DateField('Data de entrega aos RH', null=True, blank=True)
    hr_responsible = models.CharField('Responsável pela entrega aos RH', max_length=150, blank=True)
    observations = models.TextField('Observações', blank=True)
    reference = models.CharField('Referência documental', max_length=500)
    revision = models.PositiveIntegerField(default=1)

    def clean(self):
        errors = {}
        if self.issued is True and (self.training.status != 'Concluído' or self.training.end > timezone.localdate()):
            errors['issued'] = 'A emissão confirmada exige uma formação concluída, sem data final futura.'
        for prefix, status in [('learner', self.learner_delivered), ('hr', self.hr_delivered)]:
            date = getattr(self, prefix + '_date')
            responsible = getattr(self, prefix + '_responsible')
            if status is True and (not date or not responsible.strip() or self.issued is not True):
                errors[prefix + '_date'] = 'Confirme a emissão, a data e o responsável pela entrega.'
            if status is not True and (date or responsible.strip()):
                errors[prefix + '_date'] = 'Data e responsável exigem entrega confirmada.'
            if date and (date > timezone.localdate() or date < self.training.end):
                errors[prefix + '_date'] = 'A entrega deve ocorrer entre o fim da formação e hoje.'
        if not self.reference.strip():
            errors['reference'] = 'Indique a referência documental.'
        if errors:
            raise ValidationError(errors)


class PrivateDocument(models.Model):
    """PDF bytes reside in SQLite, never under STATIC_ROOT or a public media URL."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='private_documents')
    training = models.ForeignKey(Training, on_delete=models.PROTECT, null=True, blank=True, related_name='private_documents')
    kind = models.CharField(max_length=20, choices=[('cv', 'Curriculum Vitae'), ('certificate', 'Certificado')])
    content = models.BinaryField(editable=False)
    sha256 = models.CharField(max_length=64, editable=False)
    size = models.PositiveIntegerField(editable=False)
    created = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    reference = models.CharField('Referência documental', max_length=500)

    class Meta:
        ordering = ['-created']
        constraints = [models.CheckConstraint(condition=(models.Q(kind='cv', training__isnull=True) |
            models.Q(kind='certificate', training__isnull=False)), name='private_document_kind_training')]

    def clean(self):
        if self.training_id and self.training.employee_id != self.employee_id:
            raise ValidationError('A formação não pertence a este trabalhador.')


class AuditEvent(models.Model):
    created = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=60)
    detail = models.TextField()

    class Meta:
        ordering = ['-created', '-pk']


class ExecutiveReport(models.Model):
    """Immutable archived PDF, addressed by a UUID rather than a user filename."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    title = models.CharField(max_length=250)
    filters = models.JSONField(default=dict)
    summary = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)
    class Meta:
        ordering = ['-created', '-pk']


class Course(models.Model):
    class ValidationState(models.TextChoices):
        PENDING = 'pending', 'Pendente'
        VALIDATED = 'validated', 'Validada'
        BLOCKED = 'blocked', 'Bloqueada'

    source_system = models.CharField('Sistema de origem', max_length=100, blank=True, default='')
    source_action_id = models.CharField('ID da acção na origem', max_length=100, blank=True, default='')
    internal_code = models.CharField('Código interno aprovado', max_length=100, blank=True, default='')
    source_internal_code = models.CharField('Código encontrado na origem', max_length=100, blank=True, default='', editable=False)
    source_digest = models.CharField(max_length=64, blank=True, default='', editable=False)
    source_metadata = models.JSONField(default=dict, blank=True, editable=False)
    validation_status = models.CharField('Validação', max_length=20, choices=ValidationState.choices, default=ValidationState.PENDING)
    validation_notes = models.TextField('Observações de validação', blank=True, default='')
    key = models.CharField(max_length=1000, unique=True, editable=False)
    title = models.CharField('Acção de formação', max_length=1000)
    area = models.CharField('Área / Categoria', max_length=200, blank=True)
    contents = models.TextField('Conteúdos / Objectivos', blank=True)
    default_days = models.PositiveIntegerField('Dias previstos', null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(366)])
    default_hours = models.DecimalField('Horas por dia', max_digits=4, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal('.5')), MaxValueValidator(24)])
    active = models.BooleanField('Disponível no catálogo', default=True)
    next_group = models.PositiveIntegerField(default=1)
    aliases = models.JSONField(default=list, blank=True)
    revision = models.PositiveIntegerField(default=1)
    class Meta:
        ordering = ['title', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['source_system', 'source_action_id'],
                condition=~models.Q(source_system='') & ~models.Q(source_action_id=''),
                name='unique_course_source_identity'),
            models.CheckConstraint(condition=(models.Q(source_system='', source_action_id='') |
                (~models.Q(source_system='') & ~models.Q(source_action_id=''))), name='course_source_pair'),
            models.CheckConstraint(condition=models.Q(validation_status__in=['pending', 'validated', 'blocked']), name='course_validation_state'),
            models.UniqueConstraint(models.functions.Lower('internal_code'),
                condition=~models.Q(internal_code=''), name='unique_course_internal_code'),
        ]
    @property
    def total_hours(self):
        if self.default_days is None or self.default_hours is None:
            return None
        return Decimal(self.default_days) * Decimal(self.default_hours)

    @property
    def can_create_groups(self):
        return self.active and self.validation_status == self.ValidationState.VALIDATED

    def clean(self):
        super().clean()
        errors = {}
        if bool(self.source_system) != bool(self.source_action_id):
            errors['source_action_id'] = 'Indique o sistema e o ID de origem em conjunto.'
        if self.validation_status == self.ValidationState.VALIDATED:
            if not self.validation_notes.strip():
                errors['validation_notes'] = 'Documente a decisão de validação.'
            if self.source_internal_code in ('WSFR', 'WSFT') and self.internal_code not in ('WSFR', 'WSFT'):
                errors['internal_code'] = 'Resolva documentalmente o conflito WSFR/WSFT antes de validar.'
        if self.internal_code and Course.objects.using(self._state.db or 'default').exclude(pk=self.pk).filter(internal_code__iexact=self.internal_code).exists():
            errors['internal_code'] = 'Código já utilizado; reveja a correspondência.'
        if not isinstance(self.aliases, list) or any(not isinstance(a, str) for a in self.aliases):
            errors['aliases'] = 'Os aliases devem ser uma lista de títulos aprovados.'
        if errors:
            raise ValidationError(errors)
    def save(self,*args,**kwargs):
        import unicodedata
        self.key=' '.join(unicodedata.normalize('NFKC',self.title).casefold().split())
        super().save(*args,**kwargs)
    def __str__(self):
        return self.title


class TrainingGroup(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='groups')
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=1000)
    area = models.CharField(max_length=200, blank=True)
    trainer = models.CharField('Formador(a)', max_length=200)
    provider = models.CharField('Entidade formadora', max_length=200, blank=True)
    location = models.CharField('Local', max_length=200)
    capacity = models.PositiveIntegerField('Capacidade', default=30, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    hours_per_day = models.DecimalField('Horas de formação por dia', max_digits=4, decimal_places=2, validators=[MinValueValidator(Decimal('.5')), MaxValueValidator(24)])
    break_hours = models.DecimalField('Pausas por dia (horas)', max_digits=4, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(12)])
    start_time = models.TimeField('Hora de início')
    end_time = models.TimeField('Hora de fim')
    start = models.DateField()
    end = models.DateField()
    days = models.PositiveIntegerField()
    state = models.CharField(max_length=20, default='Planeada', choices=[(s,s) for s in ['Planeada','Em Curso','Concluída','Cancelada']])
    revision = models.PositiveIntegerField(default=1)
    created = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-start','-pk']
        constraints = [models.UniqueConstraint(fields=['course','number'], name='unique_course_group'), models.CheckConstraint(condition=models.Q(number__gte=1), name='positive_group_number')]
    @property
    def total_hours(self):
        return Decimal(self.days) * Decimal(self.hours_per_day)

    @property
    def code(self):return f'G{self.number}'
    def __str__(self):return f'{self.code} — {self.title}'


class GroupSession(models.Model):
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name='sessions')
    date = models.DateField('Dia de formação')
    class Meta:
        ordering = ['date']
        constraints = [models.UniqueConstraint(fields=['group','date'], name='unique_group_session')]


class Enrollment(models.Model):
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name='enrollments')
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='enrollments')
    training = models.OneToOneField(Training, on_delete=models.PROTECT, null=True, blank=True, related_name='enrollment')
    state = models.CharField(max_length=20, default='Activa', choices=[(s,s) for s in ['Activa','Cancelada','Transferida']])
    confirmation = models.CharField(max_length=30, default='Por confirmar', choices=[(s,s) for s in ['Por confirmar','Confirmado','Não participa']])
    reason = models.TextField(blank=True)
    contact_note = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['employee__name','employee__number','pk']
        constraints = [models.UniqueConstraint(fields=['group','employee'], name='unique_group_employee')]


class AssistantGuide(models.Model):
    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=180)
    keywords = models.JSONField(default=list)
    route = models.CharField(max_length=80)
    steps = models.JSONField(default=list)
    staff_only = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    updated = models.DateTimeField(auto_now=True)
    actor = models.CharField(max_length=150, blank=True)
    class Meta:
        ordering = ['title','pk']

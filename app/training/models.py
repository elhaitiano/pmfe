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
    function = models.CharField('Função', max_length=200, blank=True)
    # emp_function = models.CharField('Função execida',)
    category = models.CharField('Categoria', max_length=200, blank=True)
    area = models.CharField('Área alocada', max_length=200, blank=True)
    email = models.EmailField('E-mail', blank=True)
    phone = models.CharField('Telefone', max_length=80, blank=True)
    birth_date = models.DateField('Data de nascimento', null=True, blank=True)
    admission_date = models.DateField('Data de admissão', null=True, blank=True)
    gender = models.CharField('Género', max_length=40, blank=True)
    identity_number = models.CharField('Nº BI', max_length=80, blank=True)
    social_security_number = models.CharField('Nº INSS', max_length=80, blank=True)
    marital_status = models.CharField('Estado civil', max_length=80, blank=True)
    company = models.CharField('Empresa', max_length=200, blank=True)
    cost_center = models.CharField('Centro de Custo', max_length=200, blank=True)
    registration_origin = models.TextField('Origem do cadastro', blank=True)
    review_status = models.CharField('Estado de revisão', max_length=100, blank=True)
    registration_validation = models.CharField('Validação Cadastro EPAL', max_length=250, blank=True)
    source_row = models.PositiveIntegerField('Linha fonte EPAL', null=True, blank=True, validators=[MinValueValidator(1)])
    # Retains unmapped source fields for subsequent migration stages.
    source_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['name', 'number']

    def __str__(self):
        return f'{self.number} — {self.name}'

    @property
    def validation_status(self):
        return self.registration_validation or (self.source_data or {}).get('Validação Cadastro EPAL', 'Por confirmar')


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
    code = models.CharField('Código da formação', max_length=40, blank=True,
        help_text='Identificador do catálogo, independente do título. Pode ficar vazio enquanto não estiver confirmado.')
    key = models.CharField(max_length=1000, unique=True, editable=False)
    title = models.CharField('Acção de formação', max_length=1000)
    area = models.CharField('Área / Categoria', max_length=200, blank=True)
    contents = models.TextField('Conteúdos / Objectivos', blank=True)
    default_days = models.PositiveIntegerField('Dias previstos', default=1, validators=[MinValueValidator(1), MaxValueValidator(366)])
    default_hours = models.DecimalField('Horas por dia', max_digits=4, decimal_places=2, default=7, validators=[MinValueValidator(Decimal('.5')), MaxValueValidator(24)])
    active = models.BooleanField('Disponível no catálogo', default=True)
    next_group = models.PositiveIntegerField(default=1)
    aliases = models.JSONField(default=list, blank=True)
    revision = models.PositiveIntegerField(default=1)
    class Meta:
        ordering = ['title', 'pk']
        constraints = [models.UniqueConstraint(
            models.functions.Lower('code'), condition=~models.Q(code=''),
            name='unique_course_code_nonempty')]

    def clean(self):
        super().clean()
        self.code = self.code.strip().upper()
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list('code', flat=True).first()
            if original and original != self.code:
                raise ValidationError({'code': 'O código confirmado não pode ser alterado.'})

    def save(self,*args,**kwargs):
        import unicodedata
        self.code = self.code.strip().upper()
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

from decimal import Decimal
import uuid
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class Trainer(models.Model):
    name = models.CharField('Nome completo', max_length=200, null=False)
    organization = models.CharField('Entidade formadora', max_length=200, blank=True, null=True)
    email = models.EmailField('E-mail', blank=True, null=True)
    phone = models.CharField('Telefone', max_length=80, blank=True, null=True)
    specialty = models.CharField('Especialidade', max_length=200, blank=True, null=True)
    active = models.BooleanField('Activo', default=True, null=True)
    source_data = models.JSONField('Dados de origem', default=dict, blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        verbose_name = 'Formador'
        verbose_name_plural = 'Formadores'
        constraints = [
            models.CheckConstraint(condition=~models.Q(name=''), name='trainer_name_not_empty'),
        ]

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({'name': 'Indique o nome do formador.'})

    def __str__(self):
        return f'{self.name} — {self.organization}' if self.organization else self.name


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
    certificate_obtained = models.BooleanField('Certificado obtido', null=True, blank=True, default=None)
    certificate_obtained_date = models.DateField('Data de obtenção confirmada', null=True, blank=True)
    certificate_actor = models.CharField('Responsável pela confirmação', max_length=150, blank=True)
    certificate_evidence = models.TextField('Referência / observação administrativa', blank=True)
    certificate_issued = models.BooleanField('Certificado emitido', null=True, blank=True, default=None)
    certificate_issue_date = models.DateField('Data de emissão', null=True, blank=True)
    certificate_declaration = models.TextField('Declaração sobre o certificado', blank=True)
    certificate_declaration_source = models.TextField('Origem / evidência da declaração', blank=True)
    certificate_declared_by = models.CharField('Responsável pela declaração', max_length=200, blank=True)
    certificate_declaration_date = models.DateField('Data da declaração', null=True, blank=True)
    certificate_hr_delivered = models.BooleanField('Entregue aos RH', null=True, blank=True, default=None)
    certificate_hr_delivery_date = models.DateField('Data de entrega aos RH', null=True, blank=True)

    course = models.ForeignKey('Course', on_delete=models.PROTECT, null=True, blank=True,
        related_name='participations', verbose_name='Acção do catálogo')
    plan_status = models.CharField('Status do plano', max_length=30, default='Por confirmar',
        choices=[(x, x) for x in ['Por confirmar', 'No Plano', 'Fora do Plano']])
    source_course_code = models.CharField('Código da formação na origem', max_length=80, blank=True)
    historical_name = models.CharField('Nome histórico Pilar 3B', max_length=250, blank=True)
    identity_quality = models.CharField('Qualidade da identidade', max_length=200, blank=True)
    matching_method = models.CharField('Método de correspondência', max_length=250, blank=True)
    source_row = models.PositiveIntegerField('Linha fonte Pilar 3B', null=True, blank=True,
        validators=[MinValueValidator(1)])

    group = models.ForeignKey('TrainingGroup', on_delete=models.PROTECT, null=True, blank=True, related_name='trainings')
    attendance_confirmed = models.BooleanField('Presenças verificadas (incluindo zero)', default=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='trainings')
    title = models.CharField('Nome da formação', max_length=1000)
    area = models.CharField('Área / Categoria', max_length=200, blank=True)
    trainer_record = models.ForeignKey(
        Trainer, verbose_name='Cadastro do formador', on_delete=models.PROTECT,
        null=True, blank=True, related_name='trainings',
        help_text='Ligação ao cadastro; o nome histórico é conservado separadamente.',
    )
    trainer = models.CharField('Formador(a)', max_length=200, blank=True)
    provider = models.CharField('Entidade formadora', max_length=200, blank=True)
    location = models.CharField('Local da formação', max_length=200, blank=True)
    start = models.DateField('Data de início')
    end = models.DateField('Data de fim')
    days = models.PositiveIntegerField('Duração (dias)', null=True, blank=True, validators=[MinValueValidator(1)])
    hours_per_day = models.DecimalField('Horas por dia', max_digits=4, decimal_places=2, null=True, blank=True,
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
            models.UniqueConstraint(fields=['group', 'employee'], condition=models.Q(group__isnull=False),
                name='unique_group_employee_training'),
            models.CheckConstraint(condition=models.Q(days__gte=1), name='training_positive_days'),
            models.CheckConstraint(condition=models.Q(attendance__lte=models.F('days')), name='training_attendance'),
            models.CheckConstraint(condition=models.Q(end__gte=models.F('start')), name='training_dates'),
            models.CheckConstraint(condition=models.Q(hours_per_day__gte=Decimal('0.5'), hours_per_day__lte=24), name='training_hours'),
            models.CheckConstraint(condition=models.Q(grade__isnull=True) | models.Q(grade__gte=0, grade__lte=20), name='training_grade'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.group_id:
            if self.course_id and self.course_id != self.group.course_id:
                errors['course'] = 'A acção deve corresponder ao catálogo da turma.'
            if self.days is None or self.hours_per_day is None:
                errors['days'] = 'Uma participação de turma exige duração conhecida.'
            if not Enrollment.objects.filter(group_id=self.group_id, employee_id=self.employee_id, state='Activa').exists():
                errors['group'] = 'A participação exige uma inscrição activa do mesmo formando nesta turma.'
        if self.pk:
            linked = Enrollment.objects.filter(training_id=self.pk).first()
            if linked and (linked.employee_id != self.employee_id or linked.group_id != self.group_id):
                errors['employee'] = 'Não pode alterar a identidade ou turma de uma participação inscrita.'
            original = type(self).objects.filter(pk=self.pk).values_list('source_key', flat=True).first()
            if original and original != self.source_key:
                errors['source_key'] = 'O identificador histórico não pode ser alterado.'

        from .certificates import snapshot, validate_transition
        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        certificate_changed = snapshot(previous) != snapshot(self) if previous else any(
            getattr(self, f) is True for f in ('certificate_obtained','certificate_issued','certified','certificate_hr_delivered'))
        if certificate_changed:
            try:
                validate_transition(self)
            except ValidationError as exc:
                errors['__all__'] = exc.messages
            for date_field, flag in [('certificate_issue_date', 'certificate_issued'),
                                     ('certificate_hr_delivery_date', 'certificate_hr_delivered')]:
                value = getattr(self, date_field)
                if value and getattr(self, flag) is not True:
                    errors[flag] = 'Uma data exige confirmação do estado correspondente.'
                if value and value > timezone.localdate():
                    errors[date_field] = 'A data não pode estar no futuro.'
            if self.certificate_hr_delivered is True:
                if not self.certificate_hr_delivery_date:
                    errors['certificate_hr_delivery_date'] = 'Indique a data efectiva de entrega aos RH.'
                if self.status != 'Concluído':
                    errors['certificate_hr_delivered'] = 'Finalize a formação antes de confirmar a entrega aos RH.'
            if self.certificate_issued is False and (self.certified is True or self.certificate_hr_delivered is True):
                errors['certificate_issued'] = 'Uma entrega confirmada não é compatível com certificado não emitido.'
            for field in ['certificate_delivery_date', 'certificate_hr_delivery_date']:
                value = getattr(self, field)
                if value and self.certificate_issue_date and value < self.certificate_issue_date:
                    errors[field] = 'A entrega não pode anteceder a emissão.'
            if self.certificate_hr_delivery_date and self.end and self.certificate_hr_delivery_date < self.end:
                errors['certificate_hr_delivery_date'] = 'A entrega não pode anteceder o fim da formação.'
            if self.certificate_declaration_date and self.certificate_declaration_date > timezone.localdate():
                errors['certificate_declaration_date'] = 'A declaração não pode ter uma data futura.'
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
            # Other participation forms do not expose the certificate-specific fields.
            for name in list(errors):
                if name.startswith('certificate_') and name not in ('certificate_delivery_date', 'certificate_reference'):
                    errors.setdefault('__all__', []).append(f'{self._meta.get_field(name).verbose_name}: {errors.pop(name)}')
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.group_id and self.course_id and self.group.course_id != self.course_id:
            raise ValidationError({'course': 'A acção deve corresponder à turma.'})
        if self.pk:
            linked = Enrollment.objects.filter(training_id=self.pk).first()
            if linked and (linked.employee_id != self.employee_id or linked.group_id != self.group_id):
                raise ValidationError('A participação não pode mudar de formando ou turma após inscrição.')
            original = type(self).objects.filter(pk=self.pk).values_list('source_key', flat=True).first()
            if original and original != self.source_key:
                raise ValidationError('O identificador histórico não pode ser alterado.')
        return super().save(*args, **kwargs)

    @property
    def participation_id(self):
        return (self.source_key or '').removeprefix('pilar3b:')

    @property
    def attendance_label(self):
        return 'Verificadas' if self.attendance_confirmed else 'Por confirmar'

    @property
    def verified_hours(self):
        from .kpi_service import Scope
        from .daily_kpi import attendance_summary
        if self.pk is None:
            return None
        return attendance_summary([self], [], scope=Scope({}, 'Participação', 'all_records')).metrics['hours'].value

    @property
    def verified_attendance_percent(self):
        from .kpi_service import Scope
        from .daily_kpi import attendance_summary
        if self.pk is None:
            return None
        return attendance_summary([self], [], scope=Scope({}, 'Participação', 'all_records')).metrics['attendance'].value

    @property
    def completed_hours(self):
        if self.hours_per_day is None:
            return None
        return self.attendance * self.hours_per_day

    @property
    def planned_hours(self):
        return self.days * self.hours_per_day if self.days is not None and self.hours_per_day is not None else None

    @property
    def absences(self):
        return self.days - self.attendance if self.days is not None and self.status in ('Concluído', 'Desistência') and (self.attendance > 0 or self.attendance_confirmed) else None

    @property
    def certificate_label(self):
        return 'Sim' if self.certified is True else 'Não' if self.certified is False else 'Por confirmar'

    @property
    def attendance_percent(self):
        if not self.days or self.status == 'Planeado' or (self.attendance == 0 and not self.attendance_confirmed):
            return None
        from decimal import ROUND_HALF_UP
        return (Decimal(self.attendance) * 100 / self.days).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)

    @property
    def certificate_issue_label(self):
        return 'Sim' if self.certificate_issued is True else 'Não' if self.certificate_issued is False else 'Por confirmar'

    @property
    def certificate_hr_label(self):
        return 'Sim' if self.certificate_hr_delivered is True else 'Não' if self.certificate_hr_delivered is False else 'Por confirmar'


class PrivateDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='documents')
    training = models.ForeignKey(Training, on_delete=models.PROTECT, null=True, blank=True, related_name='documents')
    kind = models.CharField('Tipo de documento', max_length=30, choices=[
        ('cv', 'CV'), ('identity', 'Bilhete de Identidade'), ('certificate', 'Certificado'),
        ('participant_receipt', 'Comprovativo de entrega ao participante'),
        ('hr_receipt', 'Comprovativo de entrega aos RH')])
    original_name = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    created = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    note = models.TextField('Observações', blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created', '-pk']

    def clean(self):
        if self.kind in ('cv', 'identity') and self.training_id:
            raise ValidationError({'training': 'O CV e o Bilhete de Identidade pertencem ao trabalhador, sem participação associada.'})
        if self.kind not in ('cv', 'identity') and not self.training_id:
            raise ValidationError({'training': 'Seleccione a participação do certificado.'})
        if self.training_id and self.training.employee_id != self.employee_id:
            raise ValidationError({'training': 'A participação pertence a outro trabalhador.'})



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
    def save(self,*args,**kwargs):
        if not self._state.adding:raise ValidationError('O arquivo é imutável; crie uma rectificação.')
        return super().save(*args,**kwargs)

    predecessor = models.OneToOneField('self',null=True,blank=True,on_delete=models.PROTECT,related_name='correction')
    revision = models.PositiveIntegerField(default=1)
    correction_reason = models.TextField(blank=True)
    definitions_version = models.CharField(max_length=40,default='legacy')
    class Meta:
        ordering = ['-created', '-pk']


class Course(models.Model):
    validation_status = models.CharField('Validação do catálogo', max_length=30, default='Por confirmar',
        choices=[(x, x) for x in ['Por confirmar', 'Validado', 'Rever']])
    code = models.CharField('Código da formação', max_length=40, blank=True,
        help_text='Identificador do catálogo, independente do título. Pode ficar vazio enquanto não estiver confirmado.')
    key = models.CharField(max_length=1000, unique=True, editable=False)
    title = models.CharField('Acção de formação', max_length=1000)
    area = models.CharField('Área / Categoria', max_length=200, blank=True)
    contents = models.TextField('Conteúdos / Objectivos', blank=True)
    default_days = models.PositiveIntegerField('Dias previstos', default=1, null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(366)])
    default_hours = models.DecimalField('Horas por dia', max_digits=4, decimal_places=2, default=7, null=True, blank=True, validators=[MinValueValidator(Decimal('.5')), MaxValueValidator(24)])
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
    trainer_record = models.ForeignKey(
        Trainer, verbose_name='Cadastro do formador', on_delete=models.PROTECT,
        null=True, blank=True, related_name='groups',
        help_text='Ligação ao cadastro; o nome histórico é conservado separadamente.',
    )
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
    code = models.CharField('Identificação / horário', max_length=80, default='principal')
    duration = models.DecimalField('Horas previstas', max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('.01')), MaxValueValidator(24)])
    revision = models.PositiveIntegerField(default=1)

    def clean(self):
        super().clean()
        if self.group_id and self.date and not self.group.start <= self.date <= self.group.end:
            raise ValidationError('A sessão deve estar dentro do calendário do grupo.')

    class Meta:
        ordering = ['date', 'code', 'pk']
        constraints = [models.UniqueConstraint(fields=['group','date','code'], name='unique_group_session_code'),
            models.CheckConstraint(condition=models.Q(duration__isnull=True) | models.Q(duration__gt=0, duration__lte=24), name='session_duration_range')]


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

    def clean(self):
        super().clean()
        if self.training_id:
            training = self.training
            if training.employee_id != self.employee_id or training.group_id != self.group_id:
                raise ValidationError({'training': 'A participação deve pertencer ao mesmo formando e à mesma turma.'})
            if self.state != 'Activa':
                raise ValidationError({'state': 'Uma inscrição com participação deve conservar o estado activo; registe a desistência na participação.'})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


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

# Register the evaluation models with the existing training application.
from .evaluation_models import (TrainingEvaluation, ReactionEvaluation, Exam,
                                ExamQuestion, ExamAttempt, ExamAnswer, GroupLogisticsEvaluation)
from .competency_models import (Competency, RoleProfile, RoleCompetency, EvaluationCycle,
    CycleCompetency, CycleRater, CompetencyResponse, DevelopmentAction)
from .competency_models import PrivateResponseRevision

class SessionAttendance(models.Model):
    session = models.ForeignKey(GroupSession, on_delete=models.PROTECT, related_name='attendance_records')
    training = models.ForeignKey(Training, on_delete=models.PROTECT, related_name='session_attendances')
    hours = models.DecimalField('Horas efectivamente frequentadas', max_digits=5, decimal_places=2, null=True, blank=True)
    confirmed = models.BooleanField('Verificado', default=False)
    revision = models.PositiveIntegerField(default=1)
    actor = models.CharField(max_length=150)
    updated = models.DateTimeField(auto_now=True)
    source = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['session','training'], name='unique_session_attendance'),
            models.CheckConstraint(condition=models.Q(hours__isnull=True) | models.Q(hours__gte=0, hours__lte=24), name='session_attendance_range'),
            models.CheckConstraint(condition=models.Q(confirmed=False) | models.Q(hours__isnull=False), name='confirmed_session_has_hours')]

    def clean(self):
        super().clean()
        if self.session_id and self.training_id:
            if self.training.group_id != self.session.group_id:
                raise ValidationError('A sessão e a participação devem pertencer ao mesmo grupo.')
            if self.training.course_id != self.session.group.course_id:
                raise ValidationError('A formação da participação não corresponde ao grupo.')
            if self.hours is not None and (self.session.duration is None or self.hours > self.session.duration):
                raise ValidationError('Horas frequentadas excedem a duração conhecida da sessão.')
            if self.confirmed and (self.hours is None or self.session.date > timezone.localdate()):
                raise ValidationError('Só pode confirmar horas conhecidas de uma sessão já realizada.')

class AttendanceImport(models.Model):
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name='attendance_imports')
    fingerprint = models.CharField(max_length=64)
    filename = models.CharField(max_length=255)
    actor = models.CharField(max_length=150)
    reason = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField()
    changes = models.JSONField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['group','fingerprint'], name='unique_logical_attendance_import')]

class CertificateEvent(models.Model):
    training = models.ForeignKey(Training, on_delete=models.PROTECT, related_name='certificate_events')
    recorded_at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    reason = models.TextField()
    before = models.JSONField()
    after = models.JSONField()
    class Meta:
        ordering = ['recorded_at','pk']


class WorkforceReference(models.Model):
    """Approved DRH denominator for one exact period and organisational scope."""
    start = models.DateField('Início do período')
    end = models.DateField('Fim do período')
    direction = models.CharField('Direcção (vazio = todas)',max_length=200,blank=True)
    category = models.CharField('Categoria (vazio = todas)',max_length=200,blank=True)
    gender = models.CharField('Género (vazio = todos)',max_length=40,blank=True)
    headcount = models.PositiveIntegerField('Efectivo aprovado')
    responsible = models.CharField('Responsável DRH',max_length=150)
    source = models.TextField('Referência / justificação')
    actor = models.CharField(max_length=150)
    created = models.DateTimeField(auto_now_add=True)
    approved = models.BooleanField('Confirmo a aprovação DRH deste efectivo e âmbito',default=False)

    def clean(self):
        if self.start and self.end and self.end<self.start:
            raise ValidationError({'end':'O fim não pode anteceder o início.'})
        for field in ('responsible','source'):
            if not getattr(self,field).strip():raise ValidationError({field:'Campo obrigatório.'})

    def save(self,*args,**kwargs):
        if self.pk:raise ValidationError('Referências aprovadas são imutáveis. Registe uma nova referência.')
        self.full_clean()
        return super().save(*args,**kwargs)

    def __str__(self):
        return f'DRH #{self.pk} · {self.start} a {self.end} · {self.direction or "Todas as direcções"} · {self.category or "Todas as categorias"} · {self.gender or "Todos os géneros"}'

    class Meta:
        ordering=['-created','-pk']

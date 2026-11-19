from django.db import transaction
from .training_areas import TrainingAreaField
from .interface_models import EmployeeInterfaceManager, TrainingInterfaceManager
from decimal import Decimal
import uuid
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone
from .scheduling_guard import PlanningModel, PlanningManager, ActiveSessionManager

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
        constraints = [models.CheckConstraint(condition=~models.Q(name=''), name='trainer_name_not_empty')]

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
    source_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['name', 'number']
        base_manager_name = 'all_objects'

    def __str__(self):
        return f'{self.number} — {self.name}'

    @property
    def validation_status(self):
        return self.registration_validation or (self.source_data or {}).get('Validação Cadastro EPAL', 'Por confirmar')
    registry_active = models.BooleanField(default=True, db_index=True)
    objects = EmployeeInterfaceManager()
    all_objects = models.Manager()

    @property
    def private_documents(self):
        return self.documents

class Training(models.Model):
    certificate_issued = models.BooleanField('Certificado emitido', null=True, blank=True, default=None)
    certificate_issue_date = models.DateField('Data de emissão', null=True, blank=True)
    certificate_declaration = models.TextField('Declaração sobre o certificado', blank=True)
    certificate_declaration_source = models.TextField('Origem / evidência da declaração', blank=True)
    certificate_declared_by = models.CharField('Responsável pela declaração', max_length=200, blank=True)
    certificate_declaration_date = models.DateField('Data da declaração', null=True, blank=True)
    certificate_hr_delivered = models.BooleanField('Entregue aos RH', null=True, blank=True, default=None)
    certificate_hr_delivery_date = models.DateField('Data de entrega aos RH', null=True, blank=True)
    course = models.ForeignKey('Course', on_delete=models.PROTECT, null=True, blank=True, related_name='participations', verbose_name='Acção do catálogo')
    plan_status = models.CharField('Status do plano', max_length=30, default='Por confirmar', choices=[(x, x) for x in ['Por confirmar', 'No Plano', 'Fora do Plano']])
    source_course_code = models.CharField('Código da formação na origem', max_length=80, blank=True)
    historical_name = models.CharField('Nome histórico Pilar 3B', max_length=250, blank=True)
    identity_quality = models.CharField('Qualidade da identidade', max_length=200, blank=True)
    matching_method = models.CharField('Método de correspondência', max_length=250, blank=True)
    source_row = models.PositiveIntegerField('Linha fonte Pilar 3B', null=True, blank=True, validators=[MinValueValidator(1)])
    group = models.ForeignKey('TrainingGroup', on_delete=models.PROTECT, null=True, blank=True, related_name='trainings')
    attendance_confirmed = models.BooleanField('Presenças verificadas (incluindo zero)', default=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='trainings')
    title = models.CharField('Nome da formação', max_length=1000)
    area = TrainingAreaField('Área / Categoria', max_length=200, blank=True)
    trainer_record = models.ForeignKey(Trainer, verbose_name='Cadastro do formador', on_delete=models.PROTECT, null=True, blank=True, related_name='trainings', help_text='Ligação ao cadastro; o nome histórico é conservado separadamente.')
    trainer = models.CharField('Formador(a)', max_length=200, blank=True)
    provider = models.CharField('Entidade formadora', max_length=200, blank=True)
    location = models.CharField('Local da formação', max_length=200, blank=True)
    start = models.DateField('Data de início')
    end = models.DateField('Data de fim')
    days = models.DecimalField('Duração (dias)', max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal('.1'))])
    hours_per_day = models.DecimalField('Horas por dia', max_digits=4, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal('0.5')), MaxValueValidator(24)])
    attendance = models.PositiveIntegerField('N.º de presenças', null=True, blank=True)
    grade = models.DecimalField('Média final /20', max_digits=4, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(20)])
    certified = models.BooleanField('Certificado entregue', default=None, null=True, blank=True)
    result = models.CharField('Resultado final', max_length=30, default='Pendente', choices=[(x, x) for x in ['Pendente', 'Aprovado', 'Reprovado', 'Não avaliado']])
    certificate_reference = models.CharField('Nº/Ref. Certificado', max_length=150, blank=True)
    certificate_delivery_date = models.DateField('Data de entrega do certificado', null=True, blank=True)
    observations = models.TextField('Observações', blank=True)
    status = models.CharField('Situação', max_length=30, default='Concluído', choices=[('Concluído', 'Concluído'), ('Em Curso', 'Em Curso'), ('Planeado', 'Planeado'), ('Desistência', 'Desistência'), ('Por confirmar', 'Por confirmar')])
    source_key = models.CharField(max_length=80, unique=True, null=True, blank=True)
    source_data = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['-start', '-pk']
        constraints = [models.UniqueConstraint(fields=['group', 'employee'], condition=models.Q(group__isnull=False), name='unique_group_employee_training'), models.CheckConstraint(condition=models.Q(days__gte=1), name='training_positive_days'), models.CheckConstraint(condition=models.Q(attendance__lte=models.F('days')), name='training_attendance'), models.CheckConstraint(condition=models.Q(end__gte=models.F('start')), name='training_dates'), models.CheckConstraint(condition=models.Q(hours_per_day__gte=Decimal('0.5'), hours_per_day__lte=24), name='training_hours'), models.CheckConstraint(condition=models.Q(grade__isnull=True) | models.Q(grade__gte=0, grade__lte=20), name='training_grade')]
        base_manager_name = 'all_objects'

    def clean(self):
        super().clean()
        errors = {}
        if self.group_id:
            if self.course_id and self.course_id != self.group.course_id:
                errors['course'] = 'A acção deve corresponder ao catálogo do grupo.'
            if self.days is None or self.hours_per_day is None:
                errors['days'] = 'Uma participação de grupo exige duração conhecida.'
            if not Enrollment.objects.filter(group_id=self.group_id, employee_id=self.employee_id, state='Activa').exists():
                errors['group'] = 'A participação exige uma inscrição activa do mesmo formando neste grupo.'
        if self.pk:
            linked = Enrollment.objects.filter(training_id=self.pk).first()
            if linked and (linked.employee_id != self.employee_id or linked.group_id != self.group_id):
                errors['employee'] = 'Não pode alterar a identidade ou grupo de uma participação inscrita.'
            original = type(self).objects.filter(pk=self.pk).values_list('source_key', flat=True).first()
            if original and original != self.source_key:
                errors['source_key'] = 'O identificador histórico não pode ser alterado.'
        for date_field, flag in [('certificate_issue_date', 'certificate_issued'), ('certificate_hr_delivery_date', 'certificate_hr_delivered')]:
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
            if value and self.certificate_issue_date and (value < self.certificate_issue_date):
                errors[field] = 'A entrega não pode anteceder a emissão.'
        if self.certificate_hr_delivery_date and self.end and (self.certificate_hr_delivery_date < self.end):
            errors['certificate_hr_delivery_date'] = 'A entrega não pode anteceder o fim da formação.'
        if self.certificate_declaration_date and self.certificate_declaration_date > timezone.localdate():
            errors['certificate_declaration_date'] = 'A declaração não pode ter uma data futura.'
        if self.certified is True and (not self.certificate_delivery_date):
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
        if self.start and self.end and (self.end < self.start):
            errors['end'] = 'O fim não pode ser anterior ao início.'
        if self.status == 'Concluído':
            for field in ['start', 'end']:
                value = getattr(self, field)
                if value and value > timezone.localdate():
                    errors[field] = 'Uma formação concluída não pode ter datas futuras.'
        if self.attendance is not None and self.days is not None and (self.attendance > self.days):
            errors['attendance'] = 'As presenças não podem ultrapassar a duração em dias.'
        facts = (self.source_data or {}).get('_EPAL_CONSOLIDATED_FACTS', {})
        previous = type(self).all_objects.filter(pk=self.pk).first() if self.pk and facts else None
        if previous:
            if facts.get('historical_delivery_confirmed') and self.certified is True and (previous.certified is True):
                if not self.certificate_delivery_date:
                    errors.pop('certificate_delivery_date', None)
                if errors.get('certified', '').startswith('Finalize'):
                    errors.pop('certified', None)
            if self.grade is not None and self.grade == previous.grade:
                errors.pop('grade', None)
            if self.attendance == previous.attendance and self.days == previous.days:
                errors.pop('attendance', None)
        if self.certified is True:
            from .recovery_import import approved_undated_receipt
            if approved_undated_receipt(self):
                errors.pop('certificate_delivery_date', None)
                if errors.get('certified', '').startswith('Finalize'):
                    errors.pop('certified', None)
        if errors:
            for name in list(errors):
                if name.startswith('certificate_') and name not in ('certificate_delivery_date', 'certificate_reference'):
                    errors.setdefault('__all__', []).append(f'{self._meta.get_field(name).verbose_name}: {errors.pop(name)}')
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.group_id and self.course_id and (self.group.course_id != self.course_id):
            raise ValidationError({'course': 'A acção deve corresponder ao grupo.'})
        if self.pk:
            linked = Enrollment.objects.filter(training_id=self.pk).first()
            if linked and (linked.employee_id != self.employee_id or linked.group_id != self.group_id):
                raise ValidationError('A participação não pode mudar de formando ou grupo após inscrição.')
            original = type(self).objects.filter(pk=self.pk).values_list('source_key', flat=True).first()
            if original and original != self.source_key:
                raise ValidationError('O identificador histórico não pode ser alterado.')
        return super().save(*args, **kwargs)

    @property
    def presentation_result(self):
        facts = (self.indicator_source_data or {}).get('_EPAL_CONSOLIDATED_FACTS')
        if facts is not None:
            return facts.get('result') or self.result
        if self.simulated_grade is not None and self.status == 'Concluído':
            return 'Aprovado' if self.simulated_grade >= 10 else 'Reprovado'
        return self.result

    @property
    def delivery_reported(self):
        if self.certified is True or self.declared_delivery_date is not None:return True
        from .recovery_import import approved_handover
        return approved_handover(self)

    @property
    def validated_certificates(self):
        return (self.indicator_source_data or {}).get('_validated_certificates') or []

    @property
    def certified_hours(self):
        summary=((self.indicator_source_data or {}).get('_certificate_summary') or {})
        value=summary.get('display_hours',summary.get('hours'))
        return Decimal(str(value)) if value is not None else None

    @property
    def certificate_counted_elsewhere(self):
        return ((self.indicator_source_data or {}).get('_certificate_summary') or {}).get('same_certificate_as')

    @property
    def delivery_date_for_reporting(self):
        return self.certificate_delivery_date if self.certified is True and self.certificate_delivery_date else self.declared_delivery_date

    @property
    def simulated_grade(self):
        return None

    @property
    def presentation_grade(self):
        return self.simulated_grade if self.simulated_grade is not None else self.grade

    @property
    def declared_delivery_date(self):
        from datetime import date
        data = (self.indicator_source_data or {}).get('_certificate_delivery_declaration', {})
        try:
            value = date.fromisoformat(data['delivery_date'])
            return value if data.get('declared') and self.status == 'Concluído' and (value == self.end) and (value <= timezone.localdate()) else None
        except (KeyError, ValueError, TypeError):
            return None

    @property
    def delivery_receipt_associated(self):
        if hasattr(self, '_has_participant_receipt'):
            return self.certified is True and self._has_participant_receipt
        return self.certified is True and self.documents.filter(active=True, kind='participant_receipt', employee_id=self.employee_id).exists()

    @property
    def original_assessment(self):
        return (self.indicator_source_data or {}).get('_original_assessment') or {}

    @property
    def indicator_source_data(self):
        if hasattr(self, '_dashboard_source'):
            return self._dashboard_source
        return self.source_data

    @property
    def original_assessment_label(self):
        from .original_assessments import label
        return label(self.original_assessment)

    @property
    def participation_id(self):
        return (self.source_key or '').removeprefix('pilar3b:')

    @property
    def attendance_label(self):
        return 'Verificadas' if self.attendance_confirmed else 'Presenças sem registo comprovado'

    @property
    def plan_status_label(self):
        return 'Enquadramento não registado' if self.plan_status=='Por confirmar' else self.plan_status

    @property
    def completed_hours(self):
        facts = (self.indicator_source_data or {}).get('_EPAL_CONSOLIDATED_FACTS')
        if facts is not None:
            value = facts.get('attended_hours')
            return Decimal(str(value)) if value is not None else None
        if self.hours_per_day is None or self.attendance is None or (not self.attendance_confirmed) or (self.status == 'Planeado'):
            return None
        return self.attendance * self.hours_per_day

    @property
    def planned_hours(self):
        facts = (self.indicator_source_data or {}).get('_EPAL_CONSOLIDATED_FACTS')
        if facts is not None:
            value = facts.get('total_hours')
            return Decimal(str(value)) if value is not None else None
        return self.days * self.hours_per_day if self.days is not None and self.hours_per_day is not None else None

    @property
    def absences(self):
        facts = (self.indicator_source_data or {}).get('_EPAL_CONSOLIDATED_FACTS')
        if facts is not None:
            value = facts.get('absent_count')
            return Decimal(str(value)) if value is not None else None
        return self.days - self.attendance if self.days is not None and self.attendance is not None and (self.status in ('Concluído', 'Desistência')) and self.attendance_confirmed else None

    @property
    def certificate_label(self):
        return 'Entregue' if self.delivery_reported else 'Não entregue' if self.certified is False else 'Não emitido' if self.certificate_issued is False else 'Entrega sem registo' if self.certificate_issued is True else 'Emissão não documentada'

    @property
    def status_label(self):
        return 'Conclusão não documentada' if self.status == 'Por confirmar' else self.status

    @property
    def attendance_percent(self):
        facts = (self.indicator_source_data or {}).get('_EPAL_CONSOLIDATED_FACTS')
        if facts is not None:
            value = facts.get('attendance_percent')
            return Decimal(str(value)) if value is not None else None
        if not self.days or self.attendance is None or self.status == 'Planeado' or (not self.attendance_confirmed):
            return None
        from decimal import ROUND_HALF_UP
        return (Decimal(self.attendance) * 100 / self.days).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)

    @property
    def certificate_issue_label(self):
        return 'Sim' if self.certificate_issued is True else 'Não' if self.certificate_issued is False else '—'

    @property
    def certificate_archive_label(self):
        facts = (self.source_data or {}).get('_EPAL_CONSOLIDATED_FACTS', {})
        if facts.get('archived_pdf_ids'):
            return 'Arquivado'
        return 'Arquivado' if self.documents.filter(active=True, kind='certificate', employee_id=self.employee_id).exists() else 'Por arquivar'

    @property
    def certificate_hr_label(self):
        return 'Sim' if self.certificate_hr_delivered is True else 'Não' if self.certificate_hr_delivered is False else '—'
    interface_state = models.CharField(max_length=30, default='operational', db_index=True)
    objects = TrainingInterfaceManager()
    all_objects = models.Manager()

    @property
    def private_documents(self):
        return self.documents

    @property
    def attendance_days(self):
        facts = (self.indicator_source_data or {}).get('_EPAL_CONSOLIDATED_FACTS')
        if facts is not None:
            if not self.attendance_confirmed or self.attendance is None or facts.get('absent_count') is None:
                return None
            return Decimal(self.attendance) + Decimal(str(facts['absent_count']))
        return self.days if self.attendance_confirmed and self.attendance is not None else None

class PrivateDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='documents')
    training = models.ForeignKey(Training, on_delete=models.PROTECT, null=True, blank=True, related_name='documents')
    kind = models.CharField('Tipo de documento', max_length=30, choices=[('cv', 'CV'), ('identity', 'Bilhete de Identidade'), ('certificate', 'Certificado'), ('participant_receipt', 'Comprovativo de entrega ao participante'), ('hr_receipt', 'Comprovativo de entrega aos RH')])
    original_name = models.CharField(max_length=200, default='')
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
        if self.kind not in ('cv', 'identity') and (not self.training_id):
            raise ValidationError({'training': 'Seleccione a participação do certificado.'})
        if self.training_id and self.training.employee_id != self.employee_id:
            raise ValidationError({'training': 'A participação pertence a outro trabalhador.'})
    content = models.BinaryField(editable=False)
    reference = models.CharField('Referência documental', max_length=500)

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
    validation_status = models.CharField('Validação do catálogo', max_length=30, default='Por confirmar', choices=[(x, x) for x in ['Por confirmar', 'Validado', 'Rever']] + [('pending','Pendente'),('validated','Validada'),('blocked','Bloqueada')])
    code = models.CharField('Código da formação', max_length=40, blank=True, help_text='Identificador do catálogo, independente do título. Pode ficar vazio enquanto não estiver confirmado.')
    key = models.CharField(max_length=1000, unique=True, editable=False)
    title = models.CharField('Acção de formação', max_length=1000)
    area = TrainingAreaField('Área / Categoria', max_length=200, blank=True)
    contents = models.TextField('Conteúdos / Objectivos', blank=True)
    default_days = models.PositiveIntegerField('Dias previstos', default=1, null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(366)])
    default_hours = models.DecimalField('Horas por dia', max_digits=4, decimal_places=2, default=7, null=True, blank=True, validators=[MinValueValidator(Decimal('.5')), MaxValueValidator(24)])
    active = models.BooleanField('Disponível no catálogo', default=True)
    next_group = models.PositiveIntegerField(default=1)
    aliases = models.JSONField(default=list, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['title', 'pk']
        constraints = [models.UniqueConstraint(models.functions.Lower('code'), condition=~models.Q(code=''), name='unique_course_code_nonempty'), models.UniqueConstraint(fields=['source_system', 'source_action_id'], condition=~models.Q(source_system='') & ~models.Q(source_action_id=''), name='unique_course_source_identity'), models.CheckConstraint(condition=models.Q(source_system='', source_action_id='') | ~models.Q(source_system='') & ~models.Q(source_action_id=''), name='course_source_pair'), models.UniqueConstraint(models.functions.Lower('internal_code'), condition=~models.Q(internal_code=''), name='unique_course_internal_code')]

    def clean(self):
        super().clean()
        self.code = self.code.strip().upper()
        if self.validation_status=='validated':
            if not self.validation_notes.strip():raise ValidationError({'validation_notes':'A validação formal exige referência documental.'})
            if self.source_internal_code=='WSFR' and self.internal_code!='WSFT':raise ValidationError({'internal_code':'Confirme documentalmente o código aprovado.'})
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list('code', flat=True).first()
            if original and original != self.code:
                raise ValidationError({'code': 'O código confirmado não pode ser alterado.'})

    def save(self, *args, **kwargs):
        with transaction.atomic(using=kwargs.get('using') or self._state.db or 'default'):
            import unicodedata
            self.code = self.code.strip().upper()
            self.key = ' '.join(unicodedata.normalize('NFKC', self.title).casefold().split())
            super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    class ValidationState(models.TextChoices):
        PENDING = ('pending', 'Pendente')
        VALIDATED = ('validated', 'Validada')
        BLOCKED = ('blocked', 'Bloqueada')
    source_system = models.CharField('Sistema de origem', max_length=100, blank=True, default='')
    source_action_id = models.CharField('ID da acção na origem', max_length=100, blank=True, default='')
    internal_code = models.CharField('Código interno aprovado', max_length=100, blank=True, default='')
    source_internal_code = models.CharField('Código encontrado na origem', max_length=100, blank=True, default='', editable=False)
    source_digest = models.CharField(max_length=64, blank=True, default='', editable=False)
    source_metadata = models.JSONField(default=dict, blank=True, editable=False)
    validation_notes = models.TextField('Observações de validação', blank=True, default='')

    @property
    def total_hours(self):
        if self.default_days is None or self.default_hours is None:
            return None
        return Decimal(self.default_days) * Decimal(self.default_hours)

    @property
    def can_create_groups(self):
        # Documentary confirmation is historical metadata, not a planning prerequisite.
        # Explicit review/blocked records still require an identity or availability decision.
        return self.active and self.validation_status not in ('blocked', 'Rever')

class TrainingGroup(PlanningModel):
    centre = models.ForeignKey('TrainingCentre', on_delete=models.PROTECT, null=True, blank=True, related_name='groups', verbose_name='Centro')
    room = models.ForeignKey('TrainingRoom', on_delete=models.PROTECT, null=True, blank=True, related_name='groups', verbose_name='Sala')
    planning_confirmation = models.CharField('Planeamento', max_length=20, default='Provisorio', choices=[('Provisorio', 'Provisório'), ('Confirmado', 'Confirmado'), ('Por rever', 'Por rever')])
    reservation_fields = ('course_id', 'trainer_record_id', 'trainer', 'centre_id', 'room_id', 'capacity', 'hours_per_day', 'break_hours', 'start_time', 'end_time', 'start', 'end', 'days', 'state', 'planning_confirmation')
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='groups')
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=1000)
    area = TrainingAreaField(max_length=200, blank=True)
    trainer_record = models.ForeignKey(Trainer, verbose_name='Cadastro do formador', on_delete=models.PROTECT, null=True, blank=True, related_name='groups', help_text='Ligação ao cadastro; o nome histórico é conservado separadamente.')
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
    state = models.CharField(max_length=20, default='Planeada', choices=[(s, s) for s in ['Planeada', 'Em Curso', 'Concluída', 'Cancelada']])
    revision = models.PositiveIntegerField(default=1)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start', '-pk']
        constraints = [models.UniqueConstraint(fields=['course', 'number'], name='unique_course_group'), models.CheckConstraint(condition=models.Q(number__gte=1), name='positive_group_number')]

    @property
    def code(self):
        return f'G{self.number}'

    def __str__(self):
        return f'{self.code} — {self.title}'

    @property
    def total_hours(self):
        return Decimal(self.days) * Decimal(self.hours_per_day)

class GroupSession(PlanningModel):

    def delete(self, *args, **kwargs):
        raise ValidationError('As sessoes sao conservadas; reveja o calendario.')
    active = models.BooleanField(default=True)
    objects = ActiveSessionManager()
    all_objects = PlanningManager()
    reservation_fields = ('group_id', 'date', 'active')
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name='sessions')
    date = models.DateField('Dia de formação')

    class Meta:
        base_manager_name = 'all_objects'
        default_manager_name = 'objects'
        ordering = ['date']
        constraints = [models.UniqueConstraint(fields=['group', 'date'], name='unique_group_session')]

class Enrollment(models.Model):
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name='enrollments')
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='enrollments')
    training = models.OneToOneField(Training, on_delete=models.PROTECT, null=True, blank=True, related_name='enrollment')
    state = models.CharField(max_length=20, default='Activa', choices=[(s, s) for s in ['Activa', 'Cancelada', 'Transferida']])
    confirmation = models.CharField(max_length=30, default='Por confirmar', choices=[(s, s) for s in ['Por confirmar', 'Confirmado', 'Não participa']])
    reason = models.TextField(blank=True)
    contact_note = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee__name', 'employee__number', 'pk']
        constraints = [models.UniqueConstraint(fields=['group', 'employee'], name='unique_group_employee')]

    def clean(self):
        super().clean()
        if self.training_id:
            training = self.training
            if training.employee_id != self.employee_id or training.group_id != self.group_id:
                raise ValidationError({'training': 'A participação deve pertencer ao mesmo formando e à mesmo grupo.'})
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
        ordering = ['title', 'pk']
from .evaluation_models import TrainingEvaluation, ReactionEvaluation, Exam, ExamQuestion, ExamAttempt, ExamAnswer
from .direct_assessment_models import AssessmentScale, DirectAssessment
from .competency_models import Competency, RoleProfile, RoleCompetency, EvaluationCycle, CycleCompetency, CycleRater, CompetencyResponse, DevelopmentAction
from .competency_models import PrivateResponseRevision
from .scheduling_models import TrainingCentre, TrainingRoom, PlanningLock

class DocumentaryRecord(models.Model):
    """Source assertions kept outside academic results and operational KPIs."""
    source_key = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=40)
    employee = models.ForeignKey('Employee', null=True, blank=True, on_delete=models.PROTECT)
    state = models.CharField(max_length=100)
    payload = models.JSONField()
    payload_sha256 = models.CharField(max_length=64)
    package_sha256 = models.CharField(max_length=64)
    created = models.DateTimeField(auto_now_add=True)

class DocumentaryDecision(models.Model):
    record = models.ForeignKey(DocumentaryRecord, on_delete=models.PROTECT, related_name='decisions')
    actor = models.CharField(max_length=150)
    reason = models.TextField()
    evidence = models.TextField()
    rule_signature = models.CharField(max_length=64)
    resolution = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-pk']

class HistoricalDeliveryDirective(models.Model):
    key = models.CharField(max_length=80, unique=True)
    declaration = models.TextField()
    actor = models.CharField(max_length=150)
    scope = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)

class HistoricalReviewSnapshot(models.Model):
    key = models.CharField(max_length=100, unique=True)
    actor = models.CharField(max_length=150)
    report = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)

class HistoricalDocumentaryParticipation(models.Model):
    """A certificate-backed history entry with genuinely nullable missing data."""
    source_key = models.CharField(max_length=80, unique=True)
    employee = models.ForeignKey('Employee', on_delete=models.PROTECT, related_name='documentary_participations')
    title = models.CharField(max_length=1000)
    course_reference = models.CharField(max_length=80, blank=True)
    start = models.DateField()
    end = models.DateField()
    total_hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    declared_days = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    hours_per_day = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    completed = models.BooleanField(default=None, null=True, blank=True)
    learner_delivered = models.BooleanField(null=True, blank=True)
    learner_date = models.DateField(null=True, blank=True)
    hr_delivered = models.BooleanField(null=True, blank=True)
    delivery_basis = models.TextField(blank=True)
    evidence = models.JSONField(default=dict)

class HistoricalCertificatePDF(models.Model):
    training = models.ForeignKey('Training', on_delete=models.PROTECT, related_name='historical_pdfs', null=True, blank=True)
    documentary_participation = models.ForeignKey(HistoricalDocumentaryParticipation, on_delete=models.PROTECT, related_name='pdfs', null=True, blank=True)
    source_record = models.ForeignKey(DocumentaryRecord, on_delete=models.PROTECT)
    sha256 = models.CharField(max_length=64)
    content = models.BinaryField(editable=False)
    source_reference = models.TextField()
    binding_evidence = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['training', 'sha256'], name='unique_historical_training_pdf'), models.UniqueConstraint(fields=['documentary_participation', 'sha256'], name='unique_documentary_participation_pdf'), models.CheckConstraint(condition=models.Q(training__isnull=False, documentary_participation__isnull=True) | models.Q(training__isnull=True, documentary_participation__isnull=False), name='historical_pdf_one_participation')]

class ConsolidationRun(models.Model):
    key = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=False)
    master_sha256 = models.CharField(max_length=64)
    report = models.JSONField(default=dict)
    created = models.DateTimeField(auto_now_add=True)

class OfficialRosterEntry(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.PROTECT, related_name='official_roster_entry')
    current = models.BooleanField(default=True)
    source_sha256 = models.CharField(max_length=64)
    source_row = models.PositiveIntegerField()
    evidence = models.JSONField(default=dict)

class ConsolidatedEdition(models.Model):
    source_key = models.CharField(max_length=80, unique=True)
    course = models.ForeignKey('Course', null=True, blank=True, on_delete=models.PROTECT, related_name='consolidated_editions')
    course_reference = models.CharField(max_length=1000)
    title = models.CharField(max_length=1000)
    group_label = models.CharField(max_length=100, blank=True)
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=1000, blank=True)
    trainer = models.CharField(max_length=1000, blank=True)
    provider = models.CharField(max_length=500, blank=True)
    declared_days = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    total_hours = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    hours_per_day = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    expected_dates = models.JSONField(default=list)
    evidence = models.JSONField(default=dict)
    active = models.BooleanField(default=True)

class ConsolidatedParticipation(models.Model):
    source_key = models.CharField(max_length=80, unique=True)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='consolidated_participations')
    edition = models.ForeignKey(ConsolidatedEdition, on_delete=models.PROTECT, related_name='participations')
    present_count = models.PositiveIntegerField(null=True, blank=True)
    absent_count = models.PositiveIntegerField(null=True, blank=True)
    attendance_complete = models.BooleanField(default=False)
    attendance_percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    attended_hours = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    grades = models.JSONField(default=list)
    result = models.CharField(max_length=500, blank=True)
    completed = models.BooleanField(null=True, blank=True)
    legacy_training_ids = models.JSONField(default=list)
    documentary_participation_ids = models.JSONField(default=list)
    evidence = models.JSONField(default=dict)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['employee', 'edition'], name='one_consolidated_employee_edition')]

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
    historical_delivery_basis = models.TextField(blank=True)

    def clean(self):
        errors = {}
        if self.issued is True and (self.training.status != 'Concluído' or self.training.end > timezone.localdate()):
            errors['issued'] = 'A emissão confirmada exige uma formação concluída, sem data final futura.'
        for prefix, status in [('learner', self.learner_delivered), ('hr', self.hr_delivered)]:
            date = getattr(self, prefix + '_date')
            responsible = getattr(self, prefix + '_responsible')
            historical = prefix == 'learner' and bool(self.historical_delivery_basis.strip()) and (self.training.status == 'Concluído')
            if status is True and (not date and (not historical) or not responsible.strip() or self.issued is not True):
                errors[prefix + '_date'] = 'Confirme a emissão, a data e o responsável pela entrega.'
            if status is not True and (date or responsible.strip()):
                errors[prefix + '_date'] = 'Data e responsável exigem entrega confirmada.'
            if date and (date > timezone.localdate() or date < self.training.end):
                errors[prefix + '_date'] = 'A entrega deve ocorrer entre o fim da formação e hoje.'
        if not self.reference.strip():
            errors['reference'] = 'Indique a referência documental.'
        if errors:
            raise ValidationError(errors)

class PlanningDraft(models.Model):
    """Preparation only. Never queried by historical statistics or reporting."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.PROTECT)
    title = models.CharField(max_length=1000)
    area = TrainingAreaField(max_length=200, blank=True)
    group_data = models.JSONField(default=dict, blank=True)
    owner = models.ForeignKey('auth.User', on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=1)
    state = models.CharField(max_length=12, default='draft', choices=[('draft', 'Rascunho'), ('published', 'Publicado')])
    published_group = models.OneToOneField(TrainingGroup, null=True, blank=True, on_delete=models.PROTECT)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated']
        permissions = [('publish_planningdraft', 'Publicar uma turma preparada')]
        constraints = [models.CheckConstraint(condition=models.Q(state='draft', published_group__isnull=True) | models.Q(state='published', published_group__isnull=False), name='planning_draft_publication_state')]

class DraftParticipant(models.Model):
    draft = models.ForeignKey(PlanningDraft, related_name='participants', on_delete=models.CASCADE)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    transfer_requested = models.BooleanField(default=False)
    reason = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ['employee__name', 'employee__number', 'pk']
        constraints = [models.UniqueConstraint(fields=['draft', 'employee'], name='unique_draft_participant')]

class EligibilityDecision(models.Model):
    """Specific authorisation, valid only for one participant, target and evidence."""
    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.PROTECT)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    draft = models.ForeignKey(PlanningDraft, null=True, blank=True, on_delete=models.PROTECT)
    group = models.ForeignKey(TrainingGroup, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=[('repeat', 'Repetição autorizada'), ('review', 'Histórico analisado')])
    basis = models.CharField(max_length=64)
    reason = models.TextField()
    reference = models.CharField(max_length=1000)
    actor = models.ForeignKey('auth.User', on_delete=models.PROTECT)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [('authorize_training_repeat', 'Autorizar repetição e resolver elegibilidade')]
        constraints = [models.CheckConstraint(condition=models.Q(draft__isnull=False) | models.Q(group__isnull=False), name='eligibility_decision_target')]

class HistoricalInterfaceLink(models.Model):
    participation = models.OneToOneField(ConsolidatedParticipation, on_delete=models.PROTECT, related_name='interface_link')
    training = models.OneToOneField(Training, on_delete=models.PROTECT, related_name='interface_link')

class OriginalHistorySnapshot(models.Model):
    original_training_id = models.PositiveIntegerField(unique=True)
    snapshot = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)
from .interface_chart import documented_grade_label as _documented_grade_label
Training.documented_grade_label = property(_documented_grade_label)

class TrainingAreaChange(models.Model):
    policy = models.CharField(max_length=80)
    model_name = models.CharField(max_length=50)
    object_id = models.CharField(max_length=80)
    previous_area = models.CharField(max_length=200, blank=True)
    new_area = models.CharField(max_length=200, blank=True)
    reason = models.TextField()
    created = models.DateTimeField(auto_now_add=True)

class TrainingAreaIssue(models.Model):
    model_name = models.CharField(max_length=50)
    object_id = models.CharField(max_length=80)
    title = models.CharField(max_length=1000)
    original_area = models.CharField(max_length=200, blank=True)
    reason = models.TextField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['model_name', 'object_id'], name='unique_training_area_issue')]
from .area_sync import bind as _bind_training_areas
_bind_training_areas(Course, Training, TrainingGroup, PlanningDraft)

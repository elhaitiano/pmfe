"""Direct entry is independent of attendance and certificate decisions."""
from decimal import Decimal
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.core.exceptions import ValidationError


class AssessmentScale(models.Model):
    group = models.OneToOneField('TrainingGroup', on_delete=models.PROTECT, related_name='assessment_scale')
    initial_max = models.DecimalField(max_digits=8, decimal_places=2, default=100, validators=[MinValueValidator(Decimal('.01'))])
    final_max = models.DecimalField(max_digits=8, decimal_places=2, default=100, validators=[MinValueValidator(Decimal('.01'))])
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        default_permissions = ()
        constraints = [models.CheckConstraint(condition=models.Q(initial_max__gt=0, final_max__gt=0), name='direct_scale_positive')]


class DirectAssessment(models.Model):
    enrollment = models.OneToOneField('Enrollment', on_delete=models.PROTECT, related_name='direct_assessment')
    initial_points = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    final_points = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    satisfaction = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    updated_by = models.CharField(max_length=150)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        scale = AssessmentScale.objects.filter(group_id=self.enrollment.group_id).first()
        for field, maximum in [('initial_points', 'initial_max'), ('final_points', 'final_max')]:
            value = getattr(self, field)
            if value is not None and (not scale or value > getattr(scale, maximum)):
                raise ValidationError({field: 'A pontuação exige uma escala válida e não pode ultrapassar o máximo.'})

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=models.Q(initial_points__isnull=True) | models.Q(initial_points__gte=0), name='direct_initial_nonnegative'),
            models.CheckConstraint(condition=models.Q(final_points__isnull=True) | models.Q(final_points__gte=0), name='direct_final_nonnegative'),
            models.CheckConstraint(condition=models.Q(satisfaction__isnull=True) | models.Q(satisfaction__gte=1, satisfaction__lte=5), name='direct_satisfaction_range'),
        ]

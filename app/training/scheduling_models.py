from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from .scheduling_guard import PlanningModel

class TrainingCentre(PlanningModel):
    code = models.CharField("C\u00f3digo", max_length=40, unique=True)
    name = models.CharField("Centro", max_length=200)
    active = models.BooleanField("Dispon\u00edvel", default=True)
    reservation_fields = ("active",)
    class Meta:
        ordering = ["name", "pk"]
    def clean(self):
        self.code = self.code.strip().upper()
    def __str__(self):
        return self.name

class TrainingRoom(PlanningModel):
    centre = models.ForeignKey(TrainingCentre, on_delete=models.PROTECT, related_name="rooms")
    code = models.CharField("C\u00f3digo", max_length=40)
    name = models.CharField("Sala", max_length=200)
    capacity = models.PositiveIntegerField("Capacidade", validators=[MinValueValidator(1), MaxValueValidator(1000)])
    active = models.BooleanField("Dispon\u00edvel", default=True)
    reservation_fields = ("centre_id", "capacity", "active")
    class Meta:
        ordering = ["centre__name", "name", "pk"]
        constraints = [models.UniqueConstraint(fields=["centre", "code"], name="unique_room_code_per_centre"),
                       models.CheckConstraint(condition=models.Q(capacity__gte=1, capacity__lte=1000), name="valid_room_capacity")]
    def clean(self):
        self.code = self.code.strip().upper()
    def __str__(self):
        return f"{self.centre.name} / {self.name}"

class PlanningLock(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveIntegerField(default=0)
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="singleton_planning_lock")]

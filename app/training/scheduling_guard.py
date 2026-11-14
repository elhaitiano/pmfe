"""Write boundary for the experimental scheduling workflow."""
from contextvars import ContextVar
from contextlib import contextmanager
from django.db import models, transaction
from django.db.models import F
from django.core.exceptions import ValidationError

_session_deletion = ContextVar("explicit_session_deletion", default=False)
_authorized = ContextVar("planning_write", default=False)

class PlanningQuerySet(models.QuerySet):
    def update(self, **kwargs):
        protected = set(getattr(self.model, "reservation_fields", ()))
        protected |= {f[:-3] for f in list(protected) if f.endswith("_id")}
        if protected.intersection(kwargs) and not _authorized.get():
            raise ValidationError("Use o servi\u00e7o transaccional de Planeamento.")
        return super().update(**kwargs)

    def bulk_create(self, *args, **kwargs):
        if not _authorized.get():
            raise ValidationError("Use o servi\u00e7o transaccional de Planeamento.")
        return super().bulk_create(*args, **kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if not _authorized.get():
            raise ValidationError("Use o servi\u00e7o transaccional de Planeamento.")
        return super().bulk_update(objs, fields, **kwargs)

    def delete(self):
        if self.model.__name__ == "GroupSession" and not (_authorized.get() and _session_deletion.get()):
            raise ValidationError("As sess\u00f5es s\u00e3o conservadas; reveja o calend\u00e1rio.")
        return super().delete()

class PlanningManager(models.Manager.from_queryset(PlanningQuerySet)):
    pass

class ActiveSessionManager(PlanningManager):
    def get_queryset(self):
        return super().get_queryset().filter(active=True)

class PlanningModel(models.Model):
    objects = PlanningManager()
    reservation_fields = ()
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        fields = self.reservation_fields
        if not _authorized.get():
            if self._state.adding:
                raise ValidationError("Use o servi\u00e7o transaccional de Planeamento.")
            old = type(self)._base_manager.filter(pk=self.pk).values(*fields).first()
            if old is None or any(old[f] != getattr(self, f) for f in fields):
                raise ValidationError("Use o servi\u00e7o transaccional de Planeamento.")
        return super().save(*args, **kwargs)

@contextmanager
def reservation_transaction(allow_session_deletion=False):
    from .models import PlanningLock
    with transaction.atomic():
        # This must be the first authoritative DB operation: reserve the SQLite
        # writer before reading any occupancy, including across different courses.
        if PlanningLock.objects.filter(pk=1).update(revision=F("revision") + 1) != 1:
            raise ValidationError("Controlo de Planeamento indispon\u00edvel.")
        token = _authorized.set(True)
        deletion_token = _session_deletion.set(allow_session_deletion)
        try:
            yield
        finally:
            _session_deletion.reset(deletion_token)
            _authorized.reset(token)

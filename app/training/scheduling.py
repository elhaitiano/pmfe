"""Single scheduling writer. No inference of rooms or legacy session dates."""
from datetime import date
from django.core.exceptions import ValidationError
from django.db.models import F
from .models import TrainingGroup, GroupSession, Course, TrainingRoom, Trainer
from .scheduling_guard import reservation_transaction

CONFIRMED = "Confirmado"

def effective_dates(values):
    dates = values.get("dates", [])
    if any(not isinstance(d, date) for d in dates):
        raise ValidationError("Seleccione dias efectivos v\u00e1lidos.")
    if len(dates) != len(set(dates)):
        raise ValidationError("Existem dias efectivos repetidos.")
    if len(dates) > 366:
        raise ValidationError("Seleccione no m\u00e1ximo 366 dias efectivos.")
    dates = sorted(dates)
    if dates and (dates[0] < values["start"] or dates[-1] > values["end"]):
        raise ValidationError("O in\u00edcio e o fim devem corresponder aos dias efectivos seleccionados.")
    return dates

def detect_conflicts(candidate, dates):
    matches = []
    if not dates:
        return matches
    sessions = GroupSession.objects.filter(date__in=dates).exclude(group_id=candidate.pk).exclude(group__state="Cancelada").select_related("group__course", "group__trainer_record", "group__room")
    for session in sessions:
        other = session.group
        if candidate.start_time >= other.end_time or candidate.end_time <= other.start_time:
            continue
        resources = []
        if candidate.trainer_record_id and candidate.trainer_record_id == other.trainer_record_id:
            resources.append(("Formador indispon\u00edvel",False))
        elif candidate.trainer and other.trainer and (not candidate.trainer_record_id or not other.trainer_record_id) and candidate.trainer.strip().casefold() == other.trainer.strip().casefold():
            resources.append(("Poss\u00edvel coincid\u00eancia de formador",True))
        if candidate.room_id and candidate.room_id == other.room_id:
            resources.append(("Sala ocupada",False))
        elif candidate.centre_id and candidate.centre_id == other.centre_id and (not candidate.room_id or not other.room_id):
            resources.append(("Centro coincidente com sala por definir",True))
        for resource, potential in resources:
            matches.append({"hard":other.planning_confirmation == CONFIRMED and not potential,
                "date":session.date.isoformat(),"group_id":other.pk,
                "group":f"{other.course.code or other.course_id}/{other.code}",
                "start":max(candidate.start_time,other.start_time).strftime("%H:%M"),
                "end":min(candidate.end_time,other.end_time).strftime("%H:%M"),"resource":resource})
    return matches

def conflict_message(item):
    kind = "Conflito confirmado" if item["hard"] else "Conflito potencial"
    return f'{kind}: {item["resource"]}; grupo {item["group"]}, {item["date"]}, {item["start"]}\u2013{item["end"]}. Altere o hor\u00e1rio, formador ou sala.'

def validate_reservation(group, dates):
    group.full_clean()
    if group.end < group.start or (group.end - group.start).days > 365:
        raise ValidationError("Intervalo de datas inv\u00e1lido.")
    duration = ((group.end_time.hour * 60 + group.end_time.minute) - (group.start_time.hour * 60 + group.start_time.minute)) / 60
    if duration <= 0 or float(group.hours_per_day + group.break_hours) > duration:
        raise ValidationError("Horas de forma\u00e7\u00e3o e pausas ultrapassam o hor\u00e1rio dispon\u00edvel.")
    warnings = []
    if not dates:
        warnings.append("Grupo provis\u00f3rio sem sess\u00f5es: seleccione os dias efectivos antes de confirmar.")
    if not group.room_id:
        warnings.append("Sala por definir: o local hist\u00f3rico n\u00e3o identifica uma sala.")
    if group.room_id:
        room = TrainingRoom.objects.select_related("centre").get(pk=group.room_id)
        if room.centre_id != group.centre_id:
            raise ValidationError("A sala n\u00e3o pertence ao centro seleccionado.")
        if group.planning_confirmation == CONFIRMED and (not room.active or not room.centre.active):
            raise ValidationError("A sala ou o centro n\u00e3o est\u00e1 dispon\u00edvel.")
        if group.planning_confirmation == CONFIRMED and group.capacity > room.capacity:
            raise ValidationError("A capacidade do grupo ultrapassa a capacidade da sala.")
    conflicts = detect_conflicts(group, dates)
    warnings.extend(conflict_message(c) for c in conflicts)
    if group.planning_confirmation == CONFIRMED:
        if not dates or not group.trainer_record_id or not group.centre_id or not group.room_id:
            raise ValidationError("Confirma\u00e7\u00e3o exige dias efectivos, formador identificado, centro e sala.")
        if not group.trainer_record.active:
            raise ValidationError("O formador n\u00e3o est\u00e1 dispon\u00edvel.")
        hard = [conflict_message(c) for c in conflicts if c["hard"]]
        if hard:
            raise ValidationError(hard)
    return warnings

def _audit(actor, action, detail):
    from .models import AuditEvent
    AuditEvent.objects.create(actor=str(actor)[:150], action=action, detail=detail)

def save_group(course_id, values, group_id=None, revision=None, actor="Planeamento"):
    dates = effective_dates(values)
    values = dict(values)
    if dates:
        values.update(start=dates[0],end=dates[-1])
    with reservation_transaction():
        course = Course.objects.get(pk=course_id, active=True)
        if group_id:
            group = TrainingGroup.objects.get(pk=group_id, course=course)
            if group.state != "Planeada" or group.revision != revision:
                raise ValidationError("O grupo mudou ou o calend\u00e1rio est\u00e1 fechado. Reabra a p\u00e1gina.")
            if group.enrollments.filter(state="Activa").count() > values["capacity"]:
                raise ValidationError("A capacidade n\u00e3o pode ser inferior ao n\u00famero de inscritos.")
        else:
            number = course.next_group
            Course.objects.filter(pk=course.pk).update(next_group=F("next_group") + 1)
            group = TrainingGroup(course=course, number=number, title=course.title, area=course.area)
        fields = ("provider","location","capacity","hours_per_day","break_hours","start_time","end_time","start","end","centre","room","planning_confirmation")
        for field in fields:
            if field in values:
                setattr(group, field, values[field])
        trainer = values.get("trainer")
        if isinstance(trainer, Trainer):
            trainer = Trainer.objects.get(pk=trainer.pk)
            group.trainer_record = trainer
            group.trainer = trainer.name
        elif isinstance(trainer, str):
            # Historical callers may retain textual identity only. Never infer a FK.
            group.trainer_record = None
            group.trainer = trainer
        group.days = len(dates)
        warnings = validate_reservation(group, dates)
        group.revision = group.revision + 1 if group.pk else 1
        if group.pk:
            GroupSession.all_objects.filter(group=group,active=True).update(active=False)
        group.save()
        # Keep existing session rows and identities, including inactive dates.
        desired = set(dates)
        for session in GroupSession.all_objects.filter(group=group):
            if session.active != (session.date in desired):
                session.active = session.date in desired
                session.save(update_fields=["active"])
            desired.discard(session.date)
        GroupSession.objects.bulk_create([GroupSession(group=group, date=d) for d in sorted(desired)])
        _audit(actor, "PLANNING_RESERVATION", f"Grupo {group.pk}; confirma\u00e7\u00e3o={group.planning_confirmation}; dias={len(dates)}; avisos={len(warnings)}")
        group._planning_warnings = warnings
        return group

def save_resource(instance, actor="Planeamento"):
    with reservation_transaction():
        instance.full_clean()
        if instance.pk:
            from .models import TrainingCentre
            groups = TrainingGroup.objects.filter(planning_confirmation=CONFIRMED).exclude(state="Cancelada")
            if isinstance(instance, TrainingRoom):
                relevant = groups.filter(room_id=instance.pk)
                if relevant.exists() and (not instance.active or relevant.filter(capacity__gt=instance.capacity).exists()):
                    raise ValidationError("Existem reservas confirmadas incompat\u00edveis com esta altera\u00e7\u00e3o.")
                old = TrainingRoom.objects.get(pk=instance.pk)
                if old.centre_id != instance.centre_id and old.groups.exists():
                    raise ValidationError("Uma sala com grupos associados n\u00e3o pode mudar de centro.")
            elif isinstance(instance, TrainingCentre) and not instance.active and groups.filter(centre_id=instance.pk).exists():
                raise ValidationError("O centro tem reservas confirmadas.")
        instance.save()
        _audit(actor, "PLANNING_RESOURCE", f"{instance.__class__.__name__} #{instance.pk}")
        return instance


def save_trainer(instance, actor="Planeamento"):
    """Serialize availability changes with reservation confirmations."""
    creating = instance.pk is None
    with reservation_transaction():
        instance.full_clean()
        if instance.pk and not instance.active and TrainingGroup.objects.filter(
            trainer_record_id=instance.pk, planning_confirmation=CONFIRMED
        ).exclude(state="Cancelada").exists():
            raise ValidationError("O formador tem reservas confirmadas. Reveja os grupos antes de o tornar indisponivel.")
        instance.save()
        _audit(actor, "TRAINER_CREATE" if creating else "TRAINER_UPDATE", f"Formador #{instance.pk}; activo={instance.active}")
        return instance

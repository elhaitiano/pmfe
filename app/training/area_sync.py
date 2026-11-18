"""Keep one principal category across catalogue, groups and participations."""
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, pre_save
from django.utils import timezone
from .training_areas import normalize_area


def check_course(sender, instance, raw=False, using='default', **kwargs):
    if raw or not instance.course_id:
        return
    from .models import Course
    actual = Course.objects.using(using).values_list('area',flat=True).get(pk=instance.course_id)
    try:
        chosen = normalize_area(instance.area)
    except ValueError as exc:
        raise ValidationError({'area':str(exc)}) from exc
    if chosen and chosen != actual:
        raise ValidationError({'area':'A área deve coincidir com a categoria principal do catálogo. Classifique ou corrija primeiro a formação no catálogo.'})
    instance.area = actual


def propagate(sender, instance, raw=False, using='default', **kwargs):
    if raw:
        return
    from .models import Training, TrainingGroup, PlanningDraft, TrainingAreaIssue, TrainingAreaChange
    category = normalize_area(instance.area)
    for Model in [Training,TrainingGroup,PlanningDraft]:
        manager=Model._base_manager.using(using)
        for obj in manager.filter(course_id=instance.pk).exclude(area=category).only('pk','area'):
            TrainingAreaChange.objects.using(using).create(policy='catalogue-update',model_name=Model.__name__,object_id=str(obj.pk),previous_area=obj.area,new_area=category,reason='Actualização explícita da categoria principal no catálogo.')
        manager.filter(course_id=instance.pk).exclude(area=category).update(area=category)
    if category:
        TrainingAreaIssue.objects.using(using).filter(model_name='Course',object_id=str(instance.pk),resolved_at__isnull=True).update(resolved_at=timezone.now())
    else:
        TrainingAreaIssue.objects.using(using).update_or_create(model_name='Course',object_id=str(instance.pk),defaults={'title':instance.title,'resolved_at':None,'reason':'Objectivo predominante por comprovar; categoria ainda não atribuída.'})


def bind(Course, *children):
    post_save.connect(propagate,sender=Course,dispatch_uid='pmfe-course-area-sync',weak=False)
    for child in children:
        pre_save.connect(check_course,sender=child,dispatch_uid='pmfe-course-area-'+child.__name__,weak=False)

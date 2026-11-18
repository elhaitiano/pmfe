"""Versioned, reversible classification. Original source JSON is never rewritten."""
import json
from collections import Counter
from pathlib import Path
from django.db import transaction
from .training_areas import normalize_area, key

POLICY = 'training-areas-20261105-v1'


def database_guards(database='default', *, remove=False):
    from django.db import connections
    from .training_areas import AREAS
    connection=connections[database]
    if connection.vendor!='sqlite':return
    allowed=','.join("'"+v.replace("'","''")+"'" for v in ['',*AREAS])
    with connection.cursor() as cursor:
        for model in ['course','training','traininggroup','planningdraft']:
            for event in ['insert','update']:
                name='pmfe_area_'+model+'_'+event
                cursor.execute('DROP TRIGGER IF EXISTS '+name)
                if not remove:
                    operation='INSERT' if event=='insert' else 'UPDATE OF area'
                    cursor.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON training_{model} WHEN NEW.area NOT IN ({allowed}) BEGIN SELECT RAISE(ABORT,'Área inválida: utilize uma das quatro categorias de formação.'); END")


def apply(apps, database='default'):
    Course = apps.get_model('training', 'Course')
    Change = apps.get_model('training', 'TrainingAreaChange')
    Issue = apps.get_model('training', 'TrainingAreaIssue')
    policy = json.loads((Path(__file__).parent/'resources/training-areas-20261105.json').read_text(encoding='utf8'))
    reviewed = {key(r['title']): r for r in policy['courses']}
    categories = {}; title_categories = {}; totals = Counter()
    with transaction.atomic(using=database):
        for model_name in ['Course', 'Training', 'TrainingGroup', 'PlanningDraft']:
            Model = apps.get_model('training', model_name)
            manager = Model._base_manager.using(database)
            for obj in manager.all().order_by('pk').iterator():
                old = obj.area or ''; reason = 'Normalização inequívoca da categoria existente.'
                if model_name == 'Course':
                    rule = reviewed.get(key(obj.title))
                    if rule:
                        new = rule['area']; reason = rule['reason']
                    else:
                        try: new = normalize_area(old)
                        except ValueError: new = ''
                    categories[obj.pk] = new
                    title_categories.setdefault(key(obj.title), set()).add(new)
                elif obj.course_id in categories:
                    new = categories[obj.course_id]; reason = 'Categoria principal da formação no catálogo.'
                elif len(title_categories.get(key(obj.title), set())) == 1:
                    new = next(iter(title_categories[key(obj.title)])); reason = 'Designação integral coincidente com o catálogo; apenas classificação, sem alterar associações.'
                else:
                    try: new = normalize_area(old)
                    except ValueError: new = ''
                if new != old:
                    Change.objects.using(database).get_or_create(policy=POLICY, model_name=model_name, object_id=str(obj.pk), defaults={'previous_area':old,'new_area':new,'reason':reason})
                    manager.filter(pk=obj.pk).update(area=new)
                    totals[model_name] += 1
                if not new:
                    # Course-linked participation issues are represented once, at course level.
                    if model_name == 'Course' or not obj.course_id:
                        Issue.objects.using(database).get_or_create(model_name=model_name, object_id=str(obj.pk), defaults={'original_area':old,'title':obj.title,'reason':reason if model_name=='Course' and rule else 'Categoria ausente ou desconhecida; é necessário comprovar o objectivo predominante.'})
        database_guards(database)
    return dict(totals)


def reverse(apps, database='default'):
    from django.db import connections
    Change = apps.get_model('training', 'TrainingAreaChange')
    connection = connections[database]
    database_guards(database, remove=True)
    with connection.cursor() as cursor:
        for item in Change.objects.using(database).filter(policy=POLICY).order_by('-pk'):
            model = apps.get_model('training', item.model_name)
            table = connection.ops.quote_name(model._meta.db_table)
            # Raw originals may intentionally be outside the new vocabulary.
            pk = model._meta.pk.get_db_prep_value(item.object_id, connection)
            cursor.execute(f'UPDATE {table} SET area=%s WHERE id=%s AND area=%s', [item.previous_area,pk,item.new_area])

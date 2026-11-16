"""Group training areas into the four institutional domains.

- Known old areas are mapped (Técnica, Segurança, TI / Sistemas, Soft skills,
  Comunicação, Liderança, Emocional, Cliente, Social).
- Empty areas: a participation or group inherits the domain of its catalogue
  course; otherwise the title is classified by keywords taken from the domain
  definitions. Titles that match no rule stay empty ("Área por confirmar").
- Every change is written to the audit log (action AREA_DOMAINS) with the old
  value, the new value and the reason, so it can be checked and reversed.

The rules are frozen here on purpose: later edits to training/domains.py must
not change what this migration did.
"""
import json
import re
import unicodedata
from django.db import migrations

TECNICO, GESTAO, SEGURANCA, TI = 'Técnico', 'Gestão e Soft Skills', 'Segurança', 'Tecnologia de Informação'
ACTION = 'AREA_DOMAINS'


def key(value):
    text = unicodedata.normalize('NFKD', value or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(text.casefold().split())


LEGACY = {key(k): v for k, v in {
    'Técnica': TECNICO, 'Técnico': TECNICO, 'Segurança': SEGURANCA,
    'TI / Sistemas': TI, 'Tecnologia de Informação': TI,
    'Soft skills': GESTAO, 'Comunicação': GESTAO, 'Liderança': GESTAO, 'Emocional': GESTAO,
    'Cliente': GESTAO, 'Social': GESTAO, 'Gestão e Soft Skills': GESTAO,
}.items()}

# Order matters: safety first ("segurança na manutenção" is Segurança), then IT
# ("gestão de manutenção assistida por computador" is IT), then technical, then management.
RULES = [
    (SEGURANCA, [r'seguranc', r'saude (e seguranca )?(no|do) trabalho', r'\bsst\b', r'\bhst\b', r'\bshst\b',
                 r'cloro', r'incendio', r'extint', r'espacos? confinados?', r'primeiros socorros', r'socorris',
                 r'emergencia', r'evacuac', r'\brisco', r'equipamentos? de protec', r'proteccao individual', r'protecao individual', r'\bepi\b', r'trabalhos? em altura', r'bombeir', r'acidente']),
    (TI, [r'\bexcel\b', r'\bword\b', r'power ?point', r'\boffice\b', r'informatic', r'\bgmao\b', r'\bcmms\b',
          r'assistida por computador', r'computador', r'sistemas? de informac', r'\bdigita', r'software',
          r'base de dados', r'\bsap\b', r'\boutlook\b', r'internet', r'ciberseguranc']),
    (TECNICO, [r'tratamento de agua', r'\bagua', r'\bredes?\b', r'distribuic', r'manutenc', r'equipamento',
               r'contador', r'contagem', r'leitura', r'laborator', r'analises?\b', r'producao', r'\beta\b',
               r'captac', r'bomb(a|as|agem)\b', r'electr', r'eletr', r'mecanic', r'hidraul', r'soldad', r'canaliz',
               r'condutas?', r'tubagem', r'fugas?', r'valvula', r'reservatorio', r'saneamento', r'operador',
               r'instrumentac', r'automac', r'telegestao', r'\bscada\b', r'metrolog', r'qualidade da agua']),
    (GESTAO, [r'\bgestao\b', r'secretariado', r'recursos humanos', r'comunicac', r'lideranc', r'atendimento',
              r'apresentac', r'equipas?\b', r'assertiv', r'negociac', r'relacionamento', r'\bcliente',
              r'organizac', r'administrativ', r'motivac', r'emocional', r'\betica\b', r'soft ?skills?',
              r'chefia', r'supervis', r'planeamento', r'protocolo', r'arquivo', r'contabil', r'financ',
              r'conflitos?', r'coaching', r'tempo\b', r'social']),
]
COMPILED = [(domain, [re.compile(p) for p in patterns]) for domain, patterns in RULES]


def by_title(title):
    text = key(title)
    for domain, patterns in COMPILED:
        for pattern in patterns:
            if pattern.search(text):
                return domain, f'título: {pattern.pattern}'
    return '', 'sem regra aplicável'


def classify(area, title, course_area=None):
    current = (area or '').strip()
    if current:
        mapped = LEGACY.get(key(current))
        return (mapped, 'área antiga') if mapped else (current, 'mantida')
    if course_area:
        mapped = LEGACY.get(key(course_area))
        if mapped:
            return mapped, 'domínio da acção do catálogo'
    return by_title(title)


def forwards(apps, schema_editor):
    Course = apps.get_model('training', 'Course')
    TrainingGroup = apps.get_model('training', 'TrainingGroup')
    Training = apps.get_model('training', 'Training')
    AuditEvent = apps.get_model('training', 'AuditEvent')
    changes = []
    # Courses first, so groups and participations inherit the new course domain.
    for course in Course.objects.all().only('pk', 'area', 'title'):
        new, reason = classify(course.area, course.title)
        if new != course.area:
            Course.objects.filter(pk=course.pk).update(area=new)
            changes.append({'model': 'Course', 'pk': course.pk, 'old': course.area, 'new': new, 'reason': reason})
    course_area = dict(Course.objects.values_list('pk', 'area'))
    for model_name, model in (('TrainingGroup', TrainingGroup), ('Training', Training)):
        for row in model.objects.all().only('pk', 'area', 'title', 'course_id'):
            new, reason = classify(row.area, row.title, course_area.get(row.course_id))
            if new != row.area:
                model.objects.filter(pk=row.pk).update(area=new)
                changes.append({'model': model_name, 'pk': row.pk, 'old': row.area, 'new': new, 'reason': reason})
    if changes:
        AuditEvent.objects.create(actor='migração 0029', action=ACTION,
                                  detail=json.dumps(changes, ensure_ascii=False))


def backwards(apps, schema_editor):
    AuditEvent = apps.get_model('training', 'AuditEvent')
    models = {name: apps.get_model('training', name) for name in ('Course', 'TrainingGroup', 'Training')}
    for event in AuditEvent.objects.filter(action=ACTION):
        for change in json.loads(event.detail):
            models[change['model']].objects.filter(pk=change['pk']).update(area=change['old'])
        event.delete()


class Migration(migrations.Migration):
    dependencies = [('training', '0028_certificate_obtained_workforce_reference')]
    operations = [migrations.RunPython(forwards, backwards)]

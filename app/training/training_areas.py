"""Training groupings only; independent of the 360 degree competency taxonomy."""
import re
import unicodedata
from django.core.exceptions import ValidationError
from django.db import models

TECHNICAL = 'Técnica'
MANAGEMENT = 'Gestão e Soft Skills'
SAFETY = 'Segurança'
IT = 'Tecnologias de Informação'
AREAS = (TECHNICAL, MANAGEMENT, SAFETY, IT)
AREA_CHOICES = tuple((value, value) for value in AREAS)
FILTER_CHOICES = (('', 'Todas as áreas'),) + AREA_CHOICES
AREA_COLORS = {TECHNICAL:'#2fb9d6', MANAGEMENT:'#41e4c0', SAFETY:'#fbbf24', IT:'#4b82d7'}


def key(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in text if not unicodedata.combining(c)))


_ALIASES = {}
for target, aliases in [
    (TECHNICAL, ['Técnica', 'Técnico', 'Técnicas', 'Área Técnica', 'Técnica e Operacional', 'Técnica / Operacional']),
    (MANAGEMENT, ['Gestão e Soft Skills', 'Gestão & Soft Skills', 'Gestão / Soft Skills', 'Cliente', 'Comunicação', 'Emocional', 'Liderança', 'Social', 'Soft skills', 'Competências comportamentais']),
    (SAFETY, ['Segurança', 'Segurança e Saúde no Trabalho', 'Segurança, Higiene e Saúde no Trabalho', 'SHST', 'SST']),
    (IT, ['Tecnologias de Informação', 'Tecnologias da Informação', 'Tecnologia de Informação', 'Tecnologias de Informação e Comunicação', 'TI / Sistemas', 'TI', 'TIC', 'Informática', 'IT']),
]:
    for alias in aliases:
        _ALIASES[key(alias)] = target


def normalize_area(value, *, allow_blank=True):
    literal = str(value or '').strip()
    if not literal and allow_blank:
        return ''  # Unknown is a missing value, never a fifth category.
    if key(literal) in _ALIASES:
        return _ALIASES[key(literal)]
    raise ValueError('Área de formação não reconhecida: ' + (repr(literal) if literal else '(vazia)') + '. Escolha: ' + '; '.join(AREAS) + '.')


class TrainingAreaField(models.CharField):
    """Validate forms and ordinary ORM writes, including bulk/update/import paths."""
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('max_length', 200)
        kwargs.setdefault('blank', True)
        kwargs.setdefault('choices', AREA_CHOICES)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        try:
            return normalize_area(value)
        except ValueError as exc:
            raise ValidationError(str(exc), code='invalid_area') from exc

    def pre_save(self, model_instance, add):
        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value

    def get_prep_value(self, value):
        # Lookups must remain harmless for stale URLs. Writes validate separately.
        return None if value is None else str(value)

    def get_db_prep_save(self, value, connection):
        if not hasattr(value, 'as_sql'):
            value = self.to_python(value)
        return super().get_db_prep_save(value, connection)

"""Presentation-only identity caption; no historical totals or provisional fiction."""
from django import template

register=template.Library()

@register.simple_tag(takes_context=True)
def reference_a_identity(context):
    request=context.get('request')
    if request is not None and hasattr(request,'_reference_a_identity'):
        return request._reference_a_identity
    official=bool(context.get('consolidated_mode'))
    if not official and request is not None:
        from training.consolidation import enabled
        official=enabled()
    caption=('Cadastro oficial EPAL · históricos reconciliados' if official else
             'Base provisória — aguardando validação dos RH da EPAL.')
    result={'caption':caption,'official':official}
    if request is not None:request._reference_a_identity=result
    return result

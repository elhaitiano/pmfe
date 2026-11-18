"""Reasons mirror existing server rules; no permission assignments or writes."""
def creation_reason(course, user=None):
    if user is not None and not (user.is_authenticated and user.is_active and user.is_staff):
        return 'Sem autorização de gestão do planeamento.'
    if not course.active:
        return 'Formação arquivada. Reveja a disponibilidade em Editar acção.'
    if not course.can_create_groups:
        return 'Formação por rever. Corrija a identificação ou a disponibilidade em Editar acção.'
    return ''

def deletion_reason(course, user=None):
    if user is not None and not (user.is_authenticated and user.is_active and user.is_staff):
        return 'Sem autorização de gestão do planeamento.'
    if course.source_system or course.source_action_id:
        return 'Formação importada protegida. Para arquivar, utilize Editar acção.'
    from .planning import course_deletion_allowed
    if not course_deletion_allowed(course):
        return 'Existem grupos, participações ou histórico associado. Para arquivar, utilize Editar acção.'
    return ''

def attach(courses, user):
    for course in courses:
        course.create_block_reason=creation_reason(course,user)
        course.delete_block_reason=deletion_reason(course,user)
    return courses

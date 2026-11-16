"""Portuguese page labels for the shared breadcrumb."""
from django import template

register = template.Library()

PAGE_LABELS = {
    'participation_executive': 'Participação por Direcção',
    'home': 'Início',
    'dashboard': 'Dashboard Executivo',
    'audit': 'Registo de actividade',
    'evaluation_index': 'Avaliação',
    'evaluation_detail': 'Detalhes da avaliação',
    'evaluation_edit': 'Avaliação da formação',
    'reaction_edit': 'Avaliação de reacção',
    'evaluation_profile': 'Avaliação 360°',
    'exam_create': 'Nova prova', 'exam_edit': 'Editar prova',
    'attempt_create': 'Nova tentativa', 'attempt_edit': 'Editar tentativa',
    'my_ratings': 'Minhas avaliações', 'cycle_respond': 'Responder à avaliação',
    'employee_data': 'Ficha individual', 'employee_data_select': 'Fichas Individuais',
    'employee_detail': 'Histórico de Formações',
    'employee_create': 'Novo funcionário', 'employee_edit': 'Editar funcionário',
    'employee_documents': 'Documentos do funcionário', 'employee_print': 'Ficha do funcionário',
    'training_create': 'Nova formação', 'training_edit': 'Editar formação',
    'training_delete': 'Eliminar formação', 'certificate_edit': 'Certificado',
    'batch_index': 'Notas e certificados', 'batch_edit': 'Actualizar turma',
    'planning': 'Planeamento e grupos',
    'course_create': 'Nova formação', 'course_edit': 'Editar formação',
    'course_delete': 'Eliminar formação',
    'group_create': 'Novo grupo', 'group_detail': 'Detalhes do grupo',
    'group_edit': 'Editar grupo', 'group_delete': 'Eliminar grupo',
    'group_enrol': 'Inscrever funcionários', 'enrollment_edit': 'Editar inscrição',
    'assistant_admin': 'Gerir Assistente', 'assistant_create': 'Nova orientação',
    'assistant_edit': 'Editar orientação',
    'executive_explore': 'Análise detalhada', 'executive_reports': 'Relatórios executivos',
    'trainer_index': 'Formadores', 'trainer_create': 'Novo formador', 'trainer_edit': 'Editar formador',
    'import_preview': 'Importação de dados',
    'ux_administration': 'Administração e Definições',
    'competency_index': 'Competências', 'competency_catalog': 'Catálogo de competências',
    'competency_create': 'Nova competência', 'competency_edit': 'Editar competência',
    'role_create': 'Nova função', 'role_edit': 'Editar função',
    'cycle_create': 'Novo ciclo', 'cycle_detail': 'Ciclo de avaliação',
    'cycle_edit': 'Editar ciclo', 'cycle_raters': 'Avaliadores do ciclo',
    'development_index': 'Desenvolvimento', 'development_create': 'Nova acção de desenvolvimento',
    'development_edit': 'Editar acção de desenvolvimento', 'indicator_index': 'Indicadores',
}


@register.simple_tag(takes_context=True)
def current_page_label(context):
    request = context.get('request')
    match = getattr(request, 'resolver_match', None)
    return PAGE_LABELS.get(getattr(match, 'url_name', None), 'Página actual')

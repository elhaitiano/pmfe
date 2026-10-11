from django.db import migrations

GUIDE = {'id': 'delete_course', 'title': 'Eliminar uma formação criada por engano', 'keys': ['eliminar formacao', 'apagar formacao', 'eliminar acao', 'apagar acao', 'eliminar do catalogo', 'formacao criada por engano'], 'route': 'planning', 'staff': True, 'steps': [{'text': 'Abra Planeamento e Turmas e encontre a acção no catálogo. Esta operação exige acesso de administrador. Faça uma Cópia de segurança antes de eliminar.', 'target': None}, {'text': 'Escolha Eliminar formação na acção correcta. A página indica se existem turmas ou histórico associado, incluindo títulos anteriores.', 'target': 'a[href^="/planeamento/accoes/"][href$="/eliminar/"]'}, {'text': 'Se houver turmas preparatórias elegíveis, reveja-as e elimine-as individualmente primeiro. Se existir histórico de formação, utilize Editar acção e desmarque Disponível no catálogo. Os registos devem ser conservados.', 'target': None}, {'text': 'Sem turmas nem histórico, indique o motivo, escreva ELIMINAR e confirme. A eliminação é definitiva e fica na auditoria. A ajuda não executa a operação.', 'target': 'input[name="confirm"]'}]}

def add_guide(apps, schema_editor):
    guide = GUIDE
    apps.get_model('training', 'AssistantGuide').objects.using(schema_editor.connection.alias).get_or_create(
        slug=guide['id'], defaults={
            'title': guide['title'], 'keywords': guide['keys'], 'route': guide['route'],
            'steps': guide['steps'], 'staff_only': True, 'enabled': True, 'actor': 'Sistema',
        })

class Migration(migrations.Migration):
    dependencies = [('training', '0010_assistant_overview')]
    operations = [migrations.RunPython(add_guide, migrations.RunPython.noop)]

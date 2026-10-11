from django.db import migrations

GUIDE = {'id': 'delete_group', 'title': 'Eliminar uma turma criada por engano', 'keys': ['eliminar turma', 'apagar turma', 'eliminar grupo', 'apagar grupo', 'anular turma', 'limpar teste'], 'route': 'planning', 'staff': True, 'steps': [{'text': 'Abra a turma criada por engano e escolha Eliminar turma criada por engano. Só turmas planeadas ou canceladas sem registos de formação associados podem ser eliminadas.', 'target': 'a[href$="/eliminar/"]'}, {'text': 'Confira a acção, o grupo, as datas e o número de inscrições na página de confirmação. Faça uma cópia de segurança antes de continuar.', 'target': None}, {'text': 'Indique o motivo e escreva ELIMINAR. Ao confirmar, serão removidos a turma, os dias e as inscrições preparatórias. Os técnicos e o catálogo permanecem.', 'target': 'input[name="confirm"]'}, {'text': 'O código do grupo não é reutilizado. A eliminação fica na auditoria. Turmas iniciadas têm o histórico protegido; cancelar não significa eliminar.', 'target': None}]}

def add_guide(apps, schema_editor):
    Guide=apps.get_model('training','AssistantGuide')
    g=GUIDE
    Guide.objects.get_or_create(slug=g['id'],defaults={'title':g['title'],'keywords':g['keys'],'route':g['route'],'steps':g['steps'],'staff_only':True,'enabled':True,'actor':'Sistema'})

class Migration(migrations.Migration):
    dependencies=[('training','0008_assistantguide')]
    operations=[migrations.RunPython(add_guide,migrations.RunPython.noop)]

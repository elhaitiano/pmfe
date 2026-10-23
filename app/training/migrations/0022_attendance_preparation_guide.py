from django.db import migrations

OLD = [{'text': 'Em Planeamento e Turmas, abra o grupo que pretende imprimir.', 'target': 'a[href^="/planeamento/grupos/"]'}, {'text': 'Na secção Lista de presença por dia, seleccione o dia efectivo da formação.', 'target': 'select[name="date"]'}, {'text': 'Escolha Gerar lista em Excel para obter o modelo preenchido, ou Imprimir lista PDF. Os botões ficam indisponíveis quando não há inscritos activos.', 'target': 'button[formaction$="/lista-excel/"]'}, {'text': 'Abra o Excel descarregado e use Ctrl+P. O modelo tem 12 participantes por página; chegada e rubrica permanecem em branco para preenchimento presencial.', 'target': None}]
NEW = [{'text': 'Em Planeamento, escolha Criar G… na formação pretendida, ou edite o calendário de um grupo planeado.', 'target': 'a[href*="/grupos/novo/"]'}, {'text': 'Defina os dias e seleccione o Formador(a) no cadastro. Se a lista estiver vazia, utilize Gerir formadores para registar o formador.', 'target': '#id_trainer'}, {'text': 'Escolha Guardar e preparar lista de presença. Seleccione os formandos, reveja os avisos e confirme a inscrição. Não é necessário iniciar o grupo para imprimir.', 'target': 'button[value="prepare_attendance"]'}, {'text': 'Na lista de presença do grupo, escolha um dia do calendário e Gerar lista em Excel ou Imprimir lista PDF. São necessários dias definidos e inscritos activos.', 'target': '#lista-presenca'}, {'text': 'Abra o Excel e use Ctrl+P. O modelo tem 12 participantes por página; chegada e rubrica ficam em branco para assinatura presencial.', 'target': None}]


def forward(apps, schema_editor):
    Guide = apps.get_model('training', 'AssistantGuide')
    for guide in Guide.objects.using(schema_editor.connection.alias).filter(slug='list'):
        if guide.steps == OLD:
            guide.steps = NEW
            guide.save(update_fields=['steps'])


def backward(apps, schema_editor):
    Guide = apps.get_model('training', 'AssistantGuide')
    for guide in Guide.objects.using(schema_editor.connection.alias).filter(slug='list'):
        if guide.steps == NEW:
            guide.steps = OLD
            guide.save(update_fields=['steps'])


class Migration(migrations.Migration):
    dependencies = [('training', '0021_trainer_nullable_fields')]
    operations = [migrations.RunPython(forward, backward)]

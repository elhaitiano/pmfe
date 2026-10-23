from django.db import migrations

CHANGES = {'enrol': ([{'text': 'Abra o grupo planeado pretendido e escolha Adicionar formandos.', 'target': 'a[href$="/inscrever/"]'}, {'text': 'Pesquise por nome ou número mecanográfico e filtre por direcção. Seleccione os participantes; a selecção acompanha as páginas nesta janela.', 'target': '#planSelectPage'}, {'text': 'Escolha Rever selecção. Leia os avisos: presença efectiva, ausência confirmada e participação por confirmar têm significados diferentes.', 'target': '#planSelection'}, {'text': 'Escolha a decisão para cada pessoa. Uma repetição ou transferência exige justificação; não é possível duplicar a inscrição no mesmo grupo.', 'target': 'select[name^="choice_"]'}, {'text': 'Reveja os nomes e confirme as inscrições. O servidor verifica capacidade e duplicados.', 'target': '#planConfirm'}], [{'text': 'Abra o grupo e escolha Adicionar formandos. A lista de presença contém as pessoas que vão receber a formação, não os formadores.', 'target': 'a[href$="/inscrever/"]'}, {'text': 'Pesquise por nome, número ou direcção. Quem já tem histórico ou inscrição noutro grupo da mesma formação aparece bloqueado.', 'target': '#planSelectPage'}, {'text': 'Reveja a selecção e confirme apenas novos participantes. Não há opção para repetir ou transferir a mesma formação, mesmo com justificação.', 'target': '#planConfirm'}]), 'list': ([{'text': 'Em Planeamento, escolha Criar G… na formação pretendida, ou edite o calendário de um grupo planeado.', 'target': 'a[href*="/grupos/novo/"]'}, {'text': 'Defina os dias e seleccione o Formador(a) no cadastro. Se a lista estiver vazia, utilize Gerir formadores para registar o formador.', 'target': '#id_trainer'}, {'text': 'Escolha Guardar e preparar lista de presença. Seleccione os formandos, reveja os avisos e confirme a inscrição. Não é necessário iniciar o grupo para imprimir.', 'target': 'button[value="prepare_attendance"]'}, {'text': 'Na lista de presença do grupo, escolha um dia do calendário e Gerar lista em Excel ou Imprimir lista PDF. São necessários dias definidos e inscritos activos.', 'target': '#lista-presenca'}, {'text': 'Abra o Excel e use Ctrl+P. O modelo tem 12 participantes por página; chegada e rubrica ficam em branco para assinatura presencial.', 'target': None}], [{'text': 'Em Planeamento, escolha Criar G… na formação pretendida, ou edite o calendário de um grupo planeado.', 'target': 'a[href*="/grupos/novo/"]'}, {'text': 'Defina os dias e seleccione o Formador(a) no cadastro. Se a lista estiver vazia, utilize Gerir formadores para registar o formador.', 'target': '#id_trainer'}, {'text': 'Escolha Guardar e seleccionar formandos para a lista. Seleccione os formandos, reveja os avisos e confirme a inscrição. Não é necessário iniciar o grupo para imprimir.', 'target': 'button[value="prepare_attendance"]'}, {'text': 'Na lista de presença do grupo, escolha um dia do calendário e Gerar lista dos formandos em Excel ou Imprimir lista PDF. São necessários dias definidos e inscritos activos.', 'target': '#lista-presenca'}, {'text': 'Abra o Excel e use Ctrl+P. O modelo tem 12 participantes por página; chegada e rubrica ficam em branco para assinatura presencial.', 'target': None}])}


def update(apps, schema_editor, reverse=False):
    Guide = apps.get_model('training', 'AssistantGuide')
    for slug, (old, new) in CHANGES.items():
        if reverse:
            old, new = new, old
        for guide in Guide.objects.using(schema_editor.connection.alias).filter(slug=slug):
            if guide.steps == old:
                guide.steps = new
                guide.save(update_fields=['steps'])


def forward(apps, schema_editor):
    update(apps, schema_editor)


def backward(apps, schema_editor):
    update(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [('training', '0022_attendance_preparation_guide')]
    operations = [migrations.RunPython(forward, backward)]

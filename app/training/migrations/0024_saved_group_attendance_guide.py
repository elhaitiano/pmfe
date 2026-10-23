from django.db import migrations

CHANGES = {'enrol': ([{'text': 'Abra o grupo e escolha Adicionar formandos. A lista de presença contém as pessoas que vão receber a formação, não os formadores.', 'target': 'a[href$="/inscrever/"]'}, {'text': 'Pesquise por nome, número ou direcção. Quem já tem histórico ou inscrição noutro grupo da mesma formação aparece bloqueado.', 'target': '#planSelectPage'}, {'text': 'Reveja a selecção e confirme apenas novos participantes. Não há opção para repetir ou transferir a mesma formação, mesmo com justificação.', 'target': '#planConfirm'}], [{'text': 'Guarde primeiro o grupo. Abra-o em Ver grupos e escolha Seleccionar formandos para a lista.', 'target': 'a[href$="/inscrever/"]'}, {'text': 'A selecção só está disponível em grupos planeados cuja data de fim ainda não passou. A lista mostra apenas quem não tem histórico nem inscrição na mesma formação.', 'target': '#planSelectPage'}, {'text': 'Reveja e confirme os formandos. Não são permitidas repetição ou transferência para outro grupo da mesma formação.', 'target': '#planConfirm'}]), 'list': ([{'text': 'Em Planeamento, escolha Criar G… na formação pretendida, ou edite o calendário de um grupo planeado.', 'target': 'a[href*="/grupos/novo/"]'}, {'text': 'Defina os dias e seleccione o Formador(a) no cadastro. Se a lista estiver vazia, utilize Gerir formadores para registar o formador.', 'target': '#id_trainer'}, {'text': 'Escolha Guardar e seleccionar formandos para a lista. Seleccione os formandos, reveja os avisos e confirme a inscrição. Não é necessário iniciar o grupo para imprimir.', 'target': 'button[value="prepare_attendance"]'}, {'text': 'Na lista de presença do grupo, escolha um dia do calendário e Gerar lista dos formandos em Excel ou Imprimir lista PDF. São necessários dias definidos e inscritos activos.', 'target': '#lista-presenca'}, {'text': 'Abra o Excel e use Ctrl+P. O modelo tem 12 participantes por página; chegada e rubrica ficam em branco para assinatura presencial.', 'target': None}], [{'text': 'Crie o grupo, preencha o calendário e seleccione o formador. Clique em Guardar.', 'target': 'button[value="save"]'}, {'text': 'Depois de guardar, abra o grupo em Ver grupos. Na secção Lista de presença dos formandos, escolha Seleccionar formandos para a lista.', 'target': 'a[href$="/inscrever/"]'}, {'text': 'Seleccione apenas pessoas disponíveis, reveja e confirme. Quem já tem histórico ou inscrição na mesma formação não aparece na selecção.', 'target': '#planSelectPage'}, {'text': 'Na página do grupo, seleccione o dia e gere o Excel. A opção exige calendário e inscritos, e fica disponível até à data de fim, incluindo esse dia. Grupos concluídos ou cancelados não permitem gerar novas listas.', 'target': '#lista-presenca'}, {'text': 'O Excel contém formandos, com chegada e rubrica em branco. Não é necessário iniciar o grupo para gerar a lista.', 'target': None}])}


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
    dependencies = [('training', '0023_no_repeat_enrolment_guides')]
    operations = [migrations.RunPython(forward, backward)]

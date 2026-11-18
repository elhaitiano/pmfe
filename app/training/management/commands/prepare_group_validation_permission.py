from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from training.models import TrainingGroup

class Command(BaseCommand):
    help='Preparar a permissão específica, sem atribuir a utilizadores ou grupos. Simulação por defeito.'
    def add_arguments(self,parser):
        parser.add_argument('--confirm-create-permission',action='store_true')
    def handle(self,*args,**options):
        code='validate_training_group'
        if not options['confirm_create_permission']:
            self.stdout.write('Simulação: criar training.validate_training_group, sem atribuições. Requer aprovação de integração.')
            return
        ct=ContentType.objects.get_for_model(TrainingGroup)
        _,created=Permission.objects.get_or_create(content_type=ct,codename=code,defaults={'name':'Executar validação colectiva de grupos'})
        self.stdout.write('Permissão criada; nenhuma atribuição efectuada.' if created else 'Permissão já existente; nenhuma alteração.')

"""Create a normal login for an explicitly assigned 360 evaluator, without staff rights."""
from getpass import getpass
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError
from training.models import AuditEvent


class Command(BaseCommand):
    help = 'Cria uma conta de leitura/avaliador, sem privilégios administrativos. A palavra-passe é pedida de forma privada.'

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, **options):
        username = options['username'].strip()
        model = get_user_model()
        user = model(username=username, is_staff=False, is_superuser=False)
        try:
            user.full_clean(exclude=['password'])
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc
        password = getpass('Palavra-passe: ')
        confirmation = getpass('Confirme a palavra-passe: ')
        if password != confirmation:
            raise CommandError('As palavras-passe não coincidem.')
        try:
            validate_password(password, user)
            with transaction.atomic():
                user.set_password(password)
                user.save()
                AuditEvent.objects.create(actor='CLI local', action='CREATE_EVALUATOR',
                    detail=f'Conta de leitura/avaliador criada: {username}; sem privilégios administrativos.')
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc
        except IntegrityError as exc:
            raise CommandError('A conta já existe.') from exc
        self.stdout.write(self.style.SUCCESS('Conta criada. Autorize-a num ciclo para disponibilizar a avaliação.'))

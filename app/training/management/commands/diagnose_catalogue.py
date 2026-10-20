"""Read only training metadata; emit a diagnostic JSON to stdout."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from training.catalogue_diagnostic import analyse_catalogue
from training.models import Course, Training


def select_only(execute, sql, params, many, context):
    """Reject writes before they reach the database during this command."""
    if many or not sql.lstrip().upper().startswith('SELECT '):
        raise CommandError('Diagnóstico apenas de leitura: operação SQL bloqueada.')
    return execute(sql, params, many, context)


class Command(BaseCommand):
    help = 'Diagnóstico do catálogo: só leitura, sem criar acções ou grupos. Saída JSON.'
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default', choices=list(connections))

    def handle(self, *args, **options):
        alias = options['database']
        with connections[alias].execute_wrapper(select_only):
            # No employee fields, source_data, contacts, grades or certificates.
            rows = list(Training.objects.using(alias).order_by().values(
                'title', 'area', 'days', 'hours_per_day', 'source_key'))
            courses = list(Course.objects.using(alias).order_by().values(
                'id', 'title', 'aliases'))
            report = analyse_catalogue(rows, courses)
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))

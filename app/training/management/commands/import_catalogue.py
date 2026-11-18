"""Separate simulation and explicitly approved application of catalogue metadata."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from training.catalogue_import import apply_plan, read_catalogue, simulate


class Command(BaseCommand):
    help = 'Catálogo: simulação obrigatória; aplicação apenas com plano e hash explicitamente aprovados.'
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument('workbook', type=Path)
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument('--simulate', action='store_true')
        modes.add_argument('--apply', action='store_true')
        parser.add_argument('--plan', type=Path, required=True)
        parser.add_argument('--approve', default='')
        parser.add_argument('--actor', default='')
        parser.add_argument('--decisions', type=Path, help='JSON de decisões documentadas, apenas para nova simulação.')
        parser.add_argument('--database', default='default', choices=list(connections))

    def handle(self, *args, **options):
        workbook, path = options['workbook'], options['plan']
        if path.suffix.lower() != '.json' or path.resolve() == workbook.resolve():
            raise CommandError('O plano deve ser um JSON separado do Excel.')
        if options['simulate'] and (options['approve'] or options['actor']):
            raise CommandError('A simulação não aceita aprovação nem aplicação.')
        if options['apply'] and (not options['approve'] or not options['actor']):
            raise CommandError('Aplicação exige --plan, --approve e --actor após revisão da simulação.')
        if options['apply'] and options['decisions']:
            raise CommandError('As decisões devem integrar uma nova simulação antes da aprovação do plano.')
        try:
            rows = read_catalogue(workbook)
            if options['simulate']:
                decisions = json.loads(options['decisions'].read_text(encoding='utf-8')) if options['decisions'] else None
                result = simulate(rows, options['database'], decisions)
                # Never overwrite an existing reviewed plan.
                with path.open('x', encoding='utf-8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
            else:
                plan = json.loads(path.read_text(encoding='utf-8'))
                result = apply_plan(rows, plan, options['approve'], options['actor'], options['database'])
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))

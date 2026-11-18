"""Plan first; apply/reverse only with an explicitly approved immutable plan."""
import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections

from training.catalogue_duration_batch import apply, simulate, simulate_reversal


class Command(BaseCommand):
    help = 'Prepare/apply the reviewed 29-action duration batch, or a reviewed reversal.'
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--simulate', action='store_true')
        mode.add_argument('--apply', action='store_true')
        parser.add_argument('--reverse-batch', default='')
        parser.add_argument('--plan', type=Path, required=True)
        parser.add_argument('--approve', default='')
        parser.add_argument('--actor', default='')
        parser.add_argument('--database', choices=list(connections), default='default')

    def handle(self, *args, **options):
        path = options['plan']
        if path.suffix.lower() != '.json':
            raise CommandError('Use a separate JSON plan file.')
        if options['simulate'] and (options['approve'] or options['actor']):
            raise CommandError('Simulation does not accept approval or actor.')
        if options['apply'] and (not options['approve'] or not options['actor'] or options['reverse_batch']):
            raise CommandError('Apply requires a reviewed plan, approval hash and actor; no reverse flag.')
        try:
            if options['simulate']:
                plan = (simulate_reversal(options['reverse_batch'], options['database'])
                        if options['reverse_batch'] else simulate(options['database']))
                with path.open('x', encoding='utf-8') as stream:
                    json.dump(plan, stream, ensure_ascii=False, indent=2)
                result = {'plan_digest': plan['plan_digest'], 'kind': plan['kind'],
                          'actions': len(plan['operations']), 'plan': str(path)}
            else:
                plan = json.loads(path.read_text(encoding='utf-8'))
                result = apply(plan, options['approve'], options['actor'], options['database'])
        except (OSError, ValueError, KeyError, TypeError, ValidationError, DatabaseError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))

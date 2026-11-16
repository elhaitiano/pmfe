"""Prepare an import review; writing requires an explicit, current review token."""
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from training.excel_sources import load_source, SOURCE_ERRORS
from training.integrated_import import apply_reviewed, build_plan


class Command(BaseCommand):
    help = 'Simular importação Excel. --apply exige --review-token e não aceita erros parciais.'

    def add_arguments(self, parser):
        parser.add_argument('workbook', type=Path)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--review-token', default='')
        parser.add_argument('--report', type=Path, required=True)

    def handle(self, *args, **options):
        source = options['workbook'].resolve()
        target = options['report'].resolve()
        if target == source or target.suffix.lower() != '.json':
            raise CommandError('O relatório deve ser um .json separado do Excel.')
        if options['apply'] and not options['review_token']:
            raise CommandError('Faça a simulação e indique --review-token antes de aplicar.')
        workbook = None
        try:
            # Hash the same bytes that are parsed, even if the file changes on disk later.
            from io import BytesIO
            if source.stat().st_size > 10 * 1024 * 1024:
                raise ValueError('Tamanho máximo: 10 MB.')
            data = source.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            workbook = load_source(BytesIO(data))
            with transaction.atomic():
                report = build_plan(workbook, sha).report
            if options['apply']:
                if report['can_apply']:
                    report = apply_reviewed(workbook, sha, options['review_token'])
                else:
                    report['mode'] = 'blocked'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            target.chmod(0o600)
            self.stdout.write(json.dumps({k: report[k] for k in (
                'mode', 'employees_created', 'employees_updated', 'trainings_created',
                'trainings_existing', 'errors', 'warnings', 'can_apply', 'review_token', 'totals'
            )}, ensure_ascii=False))
            if report['mode'] == 'blocked':
                raise CommandError('Importação bloqueada. Consulte o relatório; nenhum registo gravado.')
        except SOURCE_ERRORS as error:
            raise CommandError(str(error)) from error
        finally:
            if workbook:
                workbook.close()

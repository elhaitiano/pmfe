"""Regularização histórica com backup, transação e simulação por defeito."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError
from training.documentary_import import prepare_package, import_package
from training.historical_regularization import regularize


class Command(BaseCommand):
    help = 'Regularizar a declaração histórica de entrega e arquivar PDFs finais com vínculo comprovado.'

    def add_arguments(self, parser):
        parser.add_argument('--package', type=Path, required=True)
        parser.add_argument('--source-root', type=Path, required=True)
        parser.add_argument('--report', type=Path, required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        package, source, report_path = (options[key].resolve() for key in ['package', 'source_root', 'report'])
        db = settings.DATABASES['default']
        if report_path.suffix.lower() != '.json' or report_path.is_relative_to(package) or report_path.is_relative_to(source):
            raise CommandError('O relatório JSON deve ficar fora das pastas de origem.')
        if str(db['NAME']) != ':memory:' and report_path == Path(db['NAME']).resolve():
            raise CommandError('O relatório não pode substituir a base de dados.')
        try:
            if not source.is_dir():
                raise ValueError('Pasta de origem inacessível.')
            employees, _ = prepare_package(package)
            backup = None
            if options['apply']:
                path = Path(db['NAME']).resolve()
                if db['ENGINE'] != 'django.db.backends.sqlite3' or not path.is_file():
                    raise ValueError('A aplicação exige uma base SQLite existente para cópia de segurança.')
                backup = path.parent / 'backups' / ('before-historical-regularization-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.sqlite3')
                backup.parent.mkdir(exist_ok=True)
                with sqlite3.connect(path) as src, sqlite3.connect(backup) as dst:
                    src.backup(dst)
            with transaction.atomic():
                imported = import_package(package, apply=True, preserve_legacy=True)
                result = regularize(source, employees)
                if not options['apply']:
                    transaction.set_rollback(True)
            report = {'mode': 'apply' if options['apply'] else 'dry-run', 'backup': str(backup) if backup else None,
                      'import': imported, 'regularization': result}
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        except (ValueError, KeyError, OSError, ValidationError, IntegrityError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, ensure_ascii=True))

"""Importação documental separada dos resultados académicos."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from training.documentary_import import import_package, prepare_package


class Command(BaseCommand):
    help = 'Ler o pacote EPAL documental; simula por defeito, sem criar resultados académicos.'

    def add_arguments(self, parser):
        parser.add_argument('folder', type=Path)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--report', type=Path, required=True)

    def handle(self, *args, **options):
        folder = options['folder'].resolve()
        report_path = options['report'].resolve()
        if report_path.suffix != '.json' or report_path.is_relative_to(folder):
            raise CommandError('Use um relatório JSON fora da pasta do pacote.')
        configured_db = settings.DATABASES['default']['NAME']
        if str(configured_db) != ':memory:' and report_path == Path(configured_db).resolve():
            raise CommandError('O relatório não pode substituir a base de dados.')
        try:
            prepare_package(folder)
            backup = None
            if options['apply']:
                db = settings.DATABASES['default']
                if db['ENGINE'] != 'django.db.backends.sqlite3' or not Path(db['NAME']).is_file():
                    raise ValueError('Aplicação exige uma base SQLite existente para cópia de segurança.')
                source = Path(db['NAME']).resolve()
                if report_path == source:
                    raise ValueError('O relatório não pode substituir a base de dados.')
                backup = source.parent / 'backups' / ('before-documentary-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.sqlite3')
                backup.parent.mkdir(exist_ok=True)
                with sqlite3.connect(source) as src, sqlite3.connect(backup) as dst:
                    src.backup(dst)
            report = import_package(folder, apply=options['apply'])
            report['backup'] = str(backup) if backup else None
        except (ValueError, KeyError, OSError, ValidationError, IntegrityError) as exc:
            raise CommandError(str(exc)) from exc
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        self.stdout.write(json.dumps(report, ensure_ascii=False))

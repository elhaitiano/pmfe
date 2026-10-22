"""Restore only into a new directory; never overwrite an active database."""
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError


ALLOWED = re.compile(r'(?:pmf\.sqlite3|secret\.key|REPOR_COPIA\.txt|(?:reports|documents)/[0-9a-fA-F-]{36}\.pdf)\Z')


def validate_snapshot(folder):
    database = folder / 'pmf.sqlite3'
    if not database.is_file():
        raise ValueError('A cópia não contém pmf.sqlite3.')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('A base de dados falhou a verificação de integridade.')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'training_employee' not in tables or 'django_migrations' not in tables:
            raise ValueError('A base não corresponde a uma instalação PMFE.')
        if 'training_executivereport' in tables:
            for row in db.execute('SELECT id, sha256 FROM training_executivereport'):
                path = folder / 'reports' / f'{uuid.UUID(row[0])}.pdf'
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row[1]:
                    raise ValueError('Relatório ausente ou alterado: ' + str(row[0]))
        if 'training_privatedocument' in tables:
            for ident, digest, size in db.execute('SELECT id, sha256, size FROM training_privatedocument'):
                path = folder / 'documents' / f'{uuid.UUID(ident)}.pdf'
                if not path.is_file() or path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError('Documento privado ausente ou alterado: ' + str(ident))


def restore(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('A pasta de destino já existe. Escolha uma pasta nova.')
    if not destination.parent.is_dir():
        raise ValueError('A pasta principal do destino não existe.')
    with tempfile.TemporaryDirectory(prefix='.pmfe-restore-', dir=destination.parent) as temp:
        folder = Path(temp) / 'data'
        folder.mkdir(mode=0o700)
        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                names = set()
                total = 0
                for info in archive.infolist():
                    if not ALLOWED.fullmatch(info.filename) or info.filename in names or stat.S_ISLNK(info.external_attr >> 16):
                        raise ValueError('Entrada inválida ou duplicada na cópia.')
                    names.add(info.filename)
                    total += info.file_size
                    if total > 5 * 1024**3 or info.file_size > 1024**3:
                        raise ValueError('A cópia ultrapassa o limite de recuperação de 5 GB (1 GB por ficheiro).')
                    target = folder / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with archive.open(info) as src, target.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(0o600)
        else:
            shutil.copyfile(source, folder / 'pmf.sqlite3')
            (folder / 'pmf.sqlite3').chmod(0o600)
        validate_snapshot(folder)
        # Destination creation is exclusive; a concurrent request cannot be overwritten.
        destination.mkdir(mode=0o700)
        try:
            for item in folder.iterdir():
                shutil.move(str(item), destination / item.name)
        except Exception:
            shutil.rmtree(destination)
            raise
    return destination


class Command(BaseCommand):
    help = 'Recuperar cópia PMFE para uma pasta nova, validando base, relatórios e documentos.'

    def add_arguments(self, parser):
        parser.add_argument('archive')
        parser.add_argument('--destination', required=True)

    def handle(self, *args, **options):
        try:
            destination = restore(options['archive'], options['destination'])
        except (OSError, ValueError, sqlite3.DatabaseError, zipfile.BadZipFile) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Cópia recuperada em {destination}. Configure PMF_DATA_DIR com a aplicação parada.'))

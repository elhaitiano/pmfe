"""Refuse automatic migration of a different PMFE lineage; read-only inspection."""
from contextlib import closing
from pathlib import Path
import sqlite3
from django.core.exceptions import ImproperlyConfigured


def require_compatible_database(database, migration_directory):
 database=Path(database)
 if not database.is_file() or database.stat().st_size==0:return
 known={p.stem for p in Path(migration_directory).glob('[0-9]*.py')}
 try:
  with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
   tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   if 'django_migrations' not in tables:
    if any(t.startswith('training_') for t in tables):raise ImproperlyConfigured('Base PMFE sem historico de migracoes identificado. Utilize uma pasta de dados nova; nao se modifica a base anterior.')
    return
   applied={r[0] for r in connection.execute("SELECT name FROM django_migrations WHERE app='training'")}
 except sqlite3.Error as exc:raise ImproperlyConfigured('Nao foi possivel verificar a base PMFE. Nenhuma migracao foi executada.') from exc
 if applied-known:
  raise ImproperlyConfigured('Base de outra linhagem PMFE: esta branch usa a estrutura consolidada da 8088. Utilize uma pasta/volume novo; preserve a base anterior. Nao use migrate --fake nem elimine migracoes. Divergencias: '+', '.join(sorted(applied-known)))

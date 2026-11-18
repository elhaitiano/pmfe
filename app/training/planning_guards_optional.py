"""Keep database guards while allowing confirmed groups at a named location."""
import re
from django.db import migrations

def forward(apps, schema_editor):
    for name in ('f6_group_insert', 'f6_group_update'):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=%s", [name])
            sql = cursor.fetchone()[0]
        start = sql.index('SELECT CASE WHEN NEW.trainer_record_id')
        end = sql.index('SELECT CASE WHEN EXISTS(', start)
        resource = "\n        SELECT CASE WHEN NEW.trainer_record_id IS NULL OR NEW.centre_id IS NULL OR trim(NEW.location)=''\n          THEN RAISE(ABORT,'Confirmação exige formador e local.') END;\n        SELECT CASE WHEN NOT EXISTS (\n          SELECT 1 FROM training_trainingcentre c JOIN training_trainer t ON t.id=NEW.trainer_record_id\n          WHERE c.id=NEW.centre_id AND c.active=1 AND t.active=1)\n          THEN RAISE(ABORT,'Local ou formador indisponível.') END;\n        SELECT CASE WHEN NEW.room_id IS NOT NULL AND NOT EXISTS (\n          SELECT 1 FROM training_trainingroom r WHERE r.id=NEW.room_id AND r.centre_id=NEW.centre_id\n          AND r.active=1 AND r.capacity>=NEW.capacity)\n          THEN RAISE(ABORT,'Sala histórica incompatível.') END;\n        "
        schema_editor.execute('DROP TRIGGER ' + name)
        schema_editor.execute(sql[:start] + resource + sql[end:])

def backward(apps, schema_editor):
    from importlib import import_module
    old = import_module('training.migrations.0026_scheduling_write_guards')
    old.uninstall(apps, schema_editor)
    old.install(apps, schema_editor)

from django.db import migrations

def install(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        raise RuntimeError("Experimental scheduling guards require SQLite.")
    apps.get_model("training","PlanningLock").objects.get_or_create(pk=1, defaults={"revision":0})
    resource = """
    SELECT CASE WHEN NEW.trainer_record_id IS NULL OR NEW.centre_id IS NULL OR NEW.room_id IS NULL
      THEN RAISE(ABORT,'Confirmação exige formador, centro e sala.') END;
    SELECT CASE WHEN NOT EXISTS (
      SELECT 1 FROM training_trainingroom r JOIN training_trainingcentre c ON c.id=r.centre_id
      JOIN training_trainer t ON t.id=NEW.trainer_record_id
      WHERE r.id=NEW.room_id AND r.centre_id=NEW.centre_id AND r.active=1 AND c.active=1
      AND t.active=1 AND r.capacity>=NEW.capacity)
      THEN RAISE(ABORT,'Recurso indisponível ou capacidade incompatível.') END;
    """
    overlap = """
    SELECT CASE WHEN EXISTS(
      SELECT 1 FROM training_groupsession mine
      JOIN training_groupsession other ON other.date=mine.date AND other.active=1
      JOIN training_traininggroup g ON g.id=other.group_id
      WHERE mine.group_id=NEW.id AND mine.active=1 AND g.id<>NEW.id
      AND g.planning_confirmation='Confirmado' AND g.state<>'Cancelada'
      AND NEW.start_time<g.end_time AND NEW.end_time>g.start_time
      AND (g.trainer_record_id=NEW.trainer_record_id OR g.room_id=NEW.room_id))
      THEN RAISE(ABORT,'Conflito confirmado de formador ou sala.') END;
    """
    for event, suffix in [("INSERT","insert"),("UPDATE OF trainer_record_id,centre_id,room_id,capacity,start_time,end_time,planning_confirmation,state","update")]:
        schema_editor.execute("CREATE TRIGGER f6_group_"+suffix+" BEFORE "+event+
            " ON training_traininggroup WHEN NEW.planning_confirmation='Confirmado' AND NEW.state<>'Cancelada' BEGIN "+resource+overlap+" END")
    check_session = """
    SELECT CASE WHEN EXISTS(
      SELECT 1 FROM training_traininggroup mine
      JOIN training_groupsession other ON other.date=NEW.date AND other.active=1
      JOIN training_traininggroup g ON g.id=other.group_id
      WHERE mine.id=NEW.group_id AND mine.planning_confirmation='Confirmado' AND mine.state<>'Cancelada'
      AND g.id<>mine.id AND g.planning_confirmation='Confirmado' AND g.state<>'Cancelada'
      AND mine.start_time<g.end_time AND mine.end_time>g.start_time
      AND (mine.trainer_record_id=g.trainer_record_id OR mine.room_id=g.room_id))
      THEN RAISE(ABORT,'Conflito confirmado de sessão.') END;
    """
    for event, suffix in [("INSERT","insert"),("UPDATE OF date,active,group_id","update")]:
        schema_editor.execute("CREATE TRIGGER f6_session_"+suffix+" BEFORE "+event+
            " ON training_groupsession WHEN NEW.active=1 BEGIN "+check_session+" END")
    schema_editor.execute("""
    CREATE TRIGGER f6_room_update BEFORE UPDATE OF active,capacity,centre_id ON training_trainingroom
    BEGIN
      SELECT CASE WHEN EXISTS(SELECT 1 FROM training_traininggroup g WHERE g.room_id=NEW.id
        AND (g.centre_id<>NEW.centre_id OR (g.planning_confirmation='Confirmado' AND g.state<>'Cancelada'
        AND (NEW.active<>1 OR g.capacity>NEW.capacity))))
      THEN RAISE(ABORT,'A alteração da sala invalida grupos associados.') END;
    END
    """)
    schema_editor.execute("""
    CREATE TRIGGER f6_centre_update BEFORE UPDATE OF active ON training_trainingcentre
    WHEN NEW.active<>1 BEGIN
      SELECT CASE WHEN EXISTS(SELECT 1 FROM training_traininggroup g WHERE g.centre_id=NEW.id
        AND g.planning_confirmation='Confirmado' AND g.state<>'Cancelada')
      THEN RAISE(ABORT,'O centro tem reservas confirmadas.') END;
    END
    """)
    schema_editor.execute("""
    CREATE TRIGGER f6_trainer_update BEFORE UPDATE OF active ON training_trainer
    WHEN NEW.active IS NOT 1 BEGIN
      SELECT CASE WHEN EXISTS(SELECT 1 FROM training_traininggroup g WHERE g.trainer_record_id=NEW.id
        AND g.planning_confirmation='Confirmado' AND g.state<>'Cancelada')
      THEN RAISE(ABORT,'O formador tem reservas confirmadas.') END;
    END
    """)

def uninstall(apps, schema_editor):
    # Reversible DDL for isolated migration tests and a reviewed rollback.
    # No application rows or resource records are deleted by this reverse hook.
    for name in ["f6_group_insert", "f6_group_update", "f6_session_insert", "f6_session_update",
                 "f6_room_update", "f6_centre_update", "f6_trainer_update"]:
        schema_editor.execute("DROP TRIGGER IF EXISTS " + name)


class Migration(migrations.Migration):
    dependencies = [("training","0025_planning_resources_and_confirmation")]
    operations = [migrations.RunPython(install, uninstall)]

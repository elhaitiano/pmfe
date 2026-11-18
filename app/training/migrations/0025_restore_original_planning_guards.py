from django.db import migrations


def install(apps,schema_editor):
    from training.planning_guards_install import install as guards
    from training.planning_guards_optional import forward
    guards(apps,schema_editor);forward(apps,schema_editor)


def uninstall(apps,schema_editor):
    from training.planning_guards_install import uninstall
    uninstall(apps,schema_editor)


class Migration(migrations.Migration):
    dependencies=[('training','0024_alter_training_days')]
    operations=[migrations.RunPython(install,uninstall)]

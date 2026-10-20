from django.db import migrations

def preserve(apps,schema_editor):
    for t in apps.get_model('training','Training').objects.filter(certificate_delivery_date__isnull=True):
        if t.certified is not None:
            t.observations=(t.observations+' | ' if t.observations else '')+f'Controlo antigo de emissão: {t.certified}. Entrega por confirmar.'
            t.certified=None;t.save(update_fields=['observations','certified'])

class Migration(migrations.Migration):
    dependencies=[('training','0002_training_certificate_delivery_date_and_more')]
    operations=[migrations.RunPython(preserve,migrations.RunPython.noop)]

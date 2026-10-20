from django.db import migrations, models


def copy_function_from_source_data(apps, schema_editor):
    Employee = apps.get_model('training', 'Employee')
    database = schema_editor.connection.alias

    for employee in Employee.objects.using(database).only(
        'pk', 'source_data'
    ).iterator():

        source_data = employee.source_data or {}

        if not isinstance(source_data, dict):
            continue

        value = (
            source_data.get('Função exercida')
            or source_data.get('Função Exercida')
            or source_data.get('Função')
        )

        if value and str(value).strip():
            Employee.objects.using(database).filter(
                pk=employee.pk
            ).update(
                function=str(value).strip()[:200]
            )


class Migration(migrations.Migration):

    dependencies = [
        ('training', '0011_course_deletion_guidance')
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='function',
            field=models.CharField(
                blank=True,
                max_length=200,
                verbose_name='Função exercida'
            ),
        ),

        migrations.RunPython(
            copy_function_from_source_data,
            migrations.RunPython.noop
        ),
    ]
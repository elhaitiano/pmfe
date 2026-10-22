from django.db import migrations, models


def copy_function(apps, schema_editor):
    Employee = apps.get_model('training', 'Employee')
    employees = Employee.objects.using(schema_editor.connection.alias)
    for employee in employees.all().iterator():
        value = (employee.source_data or {}).get('Função')
        if value is not None:
            employees.filter(pk=employee.pk).update(function=str(value))


def preserve_function_in_source(apps, schema_editor):
    Employee = apps.get_model('training', 'Employee')
    employees = Employee.objects.using(schema_editor.connection.alias)
    for employee in employees.all().iterator():
        source = dict(employee.source_data or {})
        source['Função'] = employee.function
        employees.filter(pk=employee.pk).update(source_data=source)


class Migration(migrations.Migration):
    dependencies = [('training', '0011_course_deletion_guidance')]
    operations = [
        migrations.AddField(
            model_name='employee', name='function',
            field=models.CharField('Função', max_length=200, blank=True),
        ),
        migrations.RunPython(copy_function, preserve_function_in_source),
    ]

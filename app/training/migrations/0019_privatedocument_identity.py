from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('training', '0018_competencies_development_indicators')]

    operations = [
        migrations.AlterField(
            model_name='privatedocument',
            name='kind',
            field=models.CharField(
                choices=[
                    ('cv', 'CV'),
                    ('identity', 'Bilhete de Identidade'),
                    ('certificate', 'Certificado'),
                    ('participant_receipt', 'Comprovativo de entrega ao participante'),
                    ('hr_receipt', 'Comprovativo de entrega aos RH'),
                ],
                max_length=30,
                verbose_name='Tipo de documento',
            ),
        ),
    ]

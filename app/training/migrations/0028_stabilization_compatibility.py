"""Compatibility and non-destructive projection of preserved source fields.

This migration has only been exercised on synthetic databases. Applying it to
the official installation requires a separately approved deployment plan.
"""
from decimal import Decimal, InvalidOperation
from django.db import migrations, models

def positive_integer(value):
    try:
        number=Decimal(str(value))
        return int(number) if number.is_finite() and number==number.to_integral_value() and 0<number<=2147483647 else None
    except (ValueError,TypeError,InvalidOperation):return None

def fill_preserved_fields(apps,schema_editor):
    alias=schema_editor.connection.alias
    mappings={
        'Employee':{'gender':['Sexo'],'identity_number':['Nº BI','Nº do BI'],'social_security_number':['N.º INSS'],'marital_status':['Estado Civil'],'company':['Empresa'],'cost_center':['Centro de Custo'],'registration_origin':['Origem do cadastro'],'review_status':['Estado de revisão'],'registration_validation':['Validação Cadastro EPAL'],'source_row':['Linha fonte EPAL'],'function':['Função exercida','Função Exercida','Função']},
        'Training':{'historical_name':['Nome Histórico Pilar 3B'],'identity_quality':['Qualidade da Identidade'],'matching_method':['Método de Correspondência'],'source_row':['Linha Fonte Pilar 3B'],'source_course_code':['Código Formação'],'plan_status':['Status do Plano'],'certificate_declaration':['Estado certificado — declaração']},
    }
    for model_name,fields in mappings.items():
        model=apps.get_model('training',model_name)
        for row in model.objects.using(alias).only('pk','source_data',*fields).iterator(chunk_size=128):
            source=row.source_data
            if not isinstance(source,dict):continue
            changes={}
            for field,keys in fields.items():
                current=getattr(row,field)
                if current not in ('',None) and not (field=='plan_status' and current=='Por confirmar'):continue
                value=next((source[k] for k in keys if source.get(k) not in ('',None)),None)
                if value is None:continue
                value=positive_integer(value) if field=='source_row' else str(value).strip()
                if value in ('',None):continue
                if field=='plan_status' and value not in ('No Plano','Fora do Plano','Por confirmar'):continue
                if isinstance(value,str):value=value[:model._meta.get_field(field).max_length] if model._meta.get_field(field).max_length else value
                changes[field]=value
            if changes:model.objects.using(alias).filter(pk=row.pk).update(**changes)

class Migration(migrations.Migration):
    dependencies=[('training','0027_uniform_training_areas')]
    operations=[
        migrations.AlterField(model_name='course',name='validation_status',field=models.CharField('Validação do catálogo',max_length=30,default='Por confirmar',choices=[('Por confirmar','Por confirmar'),('Validado','Validado'),('Rever','Rever'),('pending','Pendente'),('validated','Validada'),('blocked','Bloqueada')])),
        migrations.RunPython(fill_preserved_fields,migrations.RunPython.noop),
    ]

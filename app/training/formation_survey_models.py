from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from .formation_questionnaire import digest

class FormationQuestionnaire(models.Model):
    code=models.CharField(max_length=60,unique=True)
    title=models.CharField(max_length=200)
    schema=models.JSONField()
    schema_digest=models.CharField(max_length=64)
    template_digest=models.CharField(max_length=64)
    active=models.BooleanField(default=True)
    def __str__(self):return self.title
    def clean(self):
        if digest(self.schema)!=self.schema_digest:raise ValidationError('O questionário não corresponde à versão declarada.')
        if self.pk:
            old=type(self).objects.get(pk=self.pk)
            if old.surveys.exists() and any(getattr(old,k)!=getattr(self,k) for k in ['code','schema','schema_digest','template_digest']):raise ValidationError('Versão utilizada: crie uma nova versão, sem alterar as avaliações existentes.')
    def save(self,*a,**kw):self.full_clean();return super().save(*a,**kw)

class FormationSurvey(models.Model):
    group=models.OneToOneField('training.TrainingGroup',on_delete=models.PROTECT,related_name='formation_survey')
    questionnaire=models.ForeignKey(FormationQuestionnaire,on_delete=models.PROTECT,related_name='surveys')
    questionnaire_digest=models.CharField(max_length=64)
    mode=models.CharField(max_length=12,choices=[('individual','Fichas individuais'),('aggregate','Contagens agregadas')])
    anonymous=models.BooleanField(default=True)
    evaluation_date=models.DateField()
    revision=models.PositiveIntegerField(default=1)
    class Meta:
        permissions=[('print_formation_evaluation','Imprimir fichas de avaliação da formação'),('print_personal_evaluation','Incluir identificação nas fichas de avaliação'),('export_formation_evaluation','Exportar resultados de avaliação da formação')]
    def clean(self):
        if self.questionnaire_id and (self.questionnaire_digest!=self.questionnaire.schema_digest or digest(self.questionnaire.schema)!=self.questionnaire_digest):raise ValidationError('A versão do questionário foi alterada; interrompa e confira.')
        if self.pk:
            old=type(self).objects.get(pk=self.pk)
            if (old.responses.exists() or hasattr(old,'aggregate')) and any(getattr(old,k)!=getattr(self,k) for k in ['mode','anonymous','questionnaire_id','questionnaire_digest','group_id']):raise ValidationError('O conjunto já tem respostas; não mudar método, anonimato ou versão.')

class FormationSurveyResponse(models.Model):
    survey=models.ForeignKey(FormationSurvey,on_delete=models.PROTECT,related_name='responses')
    reference=models.CharField(max_length=80)
    employee=models.ForeignKey('training.Employee',null=True,blank=True,on_delete=models.PROTECT)
    answers=models.JSONField(default=dict,blank=True)
    improvements=models.TextField(blank=True,max_length=4000)
    future_training=models.TextField(blank=True,max_length=4000)
    state=models.CharField(max_length=12,choices=[('draft','Rascunho'),('completed','Concluída')],default='draft')
    revision=models.PositiveIntegerField(default=1)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        permissions=[('save_partial_survey','Guardar respostas parciais em rascunho')]
        constraints=[models.UniqueConstraint(fields=['survey','reference'],name='one_formation_form_reference'),models.UniqueConstraint(fields=['survey','employee'],condition=Q(employee__isnull=False),name='one_named_formation_form')]
    def clean(self):
        from .formation_survey_service import validate_answers,group_people
        self.survey.clean()
        if self.survey.mode!='individual':raise ValidationError('Este conjunto utiliza contagens agregadas; não somar fichas individuais.')
        if self.survey.anonymous and self.employee_id:raise ValidationError('Uma ficha anónima não pode guardar uma associação nominal.')
        if not self.survey.anonymous and (not self.employee_id or not group_people(self.survey.group).filter(pk=self.employee_id).exists()):raise ValidationError('Seleccione um participante deste grupo.')
        validate_answers(self.survey.questionnaire.schema,self.answers)

class FormationSurveyAggregate(models.Model):
    survey=models.OneToOneField(FormationSurvey,on_delete=models.PROTECT,related_name='aggregate')
    received_forms=models.PositiveIntegerField()
    counts=models.JSONField(default=dict,blank=True)
    improvements=models.TextField(blank=True,max_length=4000)
    future_training=models.TextField(blank=True,max_length=4000)
    state=models.CharField(max_length=12,choices=[('draft','Rascunho'),('completed','Concluída')],default='draft')
    reason=models.TextField(blank=True,max_length=2000)
    revision=models.PositiveIntegerField(default=1)
    updated_at=models.DateTimeField(auto_now=True)
    def clean(self):
        from .formation_survey_service import validate_counts,group_people
        self.survey.clean()
        if self.survey.mode!='aggregate' or self.survey.responses.exists():raise ValidationError('Não misturar contagens e fichas individuais do mesmo conjunto.')
        validate_counts(self.survey.questionnaire.schema,self.counts,self.received_forms,self.state)
        if self.received_forms>group_people(self.survey.group).count() and not self.reason.strip():raise ValidationError('As fichas excedem os participantes registados. Justifique a diferença, sem criar participantes.')

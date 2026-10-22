from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Employee, Training, TrainingEvaluation, ReactionEvaluation
from .indicators import analyse_indicators


class IndicatorTests(TestCase):
    def setUp(self):
        self.today=timezone.localdate()
        self.employee=Employee.objects.create(number='IND',name='Pessoa indicadores')
        self.admin=get_user_model().objects.create_user('indicator-admin',is_staff=True)
        self.client.force_login(self.admin)

    def training(self, **changes):
        values=dict(employee=self.employee,title='Curso indicadores',start=self.today-timedelta(days=5),
            end=self.today-timedelta(days=4),days=2,hours_per_day=7,attendance=2,status='Concluído')
        values.update(changes)
        return Training.objects.create(**values)

    def verified(self, training):
        return TrainingEvaluation.objects.create(training=training,validation='Validado',date=self.today,
            evaluator='RH',source='Documento',validated_grade=training.grade,validated_result=training.result)

    def test_only_confirmed_attendance_and_zero_not_missing(self):
        yes=self.training(attendance_confirmed=True)
        zero=self.training(attendance_confirmed=True,attendance=0)
        self.training(attendance_confirmed=False)
        self.training(attendance_confirmed=True,status='Em Curso')
        self.training(attendance_confirmed=True,days=None,hours_per_day=None)
        result=analyse_indicators(Training.objects.all())
        self.assertEqual(result['attendance'],50)
        self.assertEqual(result['days'],4)
        self.assertEqual(result['presences'],2)
        self.assertEqual(result['attendance_count'],2)
        self.assertEqual(result['attendance_provisional'],3)
        self.assertEqual(sum(r['attendance'] for r in result['reconciliation']),2)

    def test_validated_learning_excludes_legacy_and_stale_classifications(self):
        first=self.training(grade=16,result='Aprovado'); self.verified(first)
        zero=self.training(grade=0,result='Reprovado'); self.verified(zero)
        legacy=self.training(grade=20,result='Aprovado')
        TrainingEvaluation.objects.create(training=legacy,validation='Validado',source='Antiga')
        pending=self.training(grade=None,result='Não avaliado'); self.verified(pending)
        result=analyse_indicators(Training.objects.all())
        self.assertEqual(result['learning'],8)
        self.assertEqual(result['learning_count'],2)
        self.assertEqual(result['approval_rate'],50)
        self.assertEqual(result['result_count'],2)
        Training.objects.filter(pk=first.pk).update(grade=15)
        result=analyse_indicators(Training.objects.all())
        self.assertEqual(result['learning'],0)
        self.assertEqual(result['learning_count'],1)
        self.assertEqual(result['approved'],0)

    def test_partial_reaction_and_logistics_have_distinct_denominators(self):
        first=self.training(); second=self.training(); third=self.training()
        evaluation=self.verified(first); evaluation.logistics=Decimal('4.5'); evaluation.save()
        ReactionEvaluation.objects.create(training=first,validation='Validado',organization=5,content=None,trainer=3,usefulness=4)
        ReactionEvaluation.objects.create(training=second,validation='Validado',organization=3,content=2)
        ReactionEvaluation.objects.create(training=third,validation='Por confirmar',organization=1,content=1)
        result=analyse_indicators(Training.objects.all())
        self.assertEqual(result['satisfaction'][0]['mean'],4)
        self.assertEqual(result['satisfaction'][0]['count'],2)
        self.assertEqual(result['satisfaction'][1]['mean'],2)
        self.assertEqual(result['satisfaction'][1]['count'],1)
        self.assertEqual(result['logistics'],Decimal('4.5'))
        self.assertIsNone(result['learning'])

    def test_certificates_keep_declarations_and_unknown_separate(self):
        self.training(certificate_issued=True,certified=True,certificate_hr_delivered=False)
        self.training(certificate_declaration='Emitido segundo declaração')
        result=analyse_indicators(Training.objects.all())['certificates']
        self.assertEqual(result['issued'],1)
        self.assertEqual(result['participant'],1)
        self.assertEqual(result['hr'],0)
        self.assertEqual(result['declarations'],1)
        self.assertEqual(result['participant_unknown'],1)

    def test_filters_empty_results_and_validation_snapshot_from_form(self):
        training=self.training()
        data={'revision':training.version,'correction_reason':'Nota confirmada','grade':'14','result':'Aprovado',
            'date':self.today,'evaluator':'RH','source':'Documento','validation':'Validado','logistics':'4'}
        self.assertEqual(self.client.post(reverse('evaluation_edit',args=[training.pk]),data).status_code,302)
        training.refresh_from_db()
        self.assertEqual(training.evaluation.validated_grade,14)
        response=self.client.get(reverse('indicator_index'))
        self.assertContains(response,'Nota final')
        self.assertEqual(response.context['learning_count'],1)
        response=self.client.get(reverse('indicator_index'),{'start':self.today.isoformat()})
        self.assertEqual(response.context['total'],0)
        self.assertIsNone(response.context['learning'])
        response=self.client.get(reverse('indicator_index'),{'start':'2026-12-10','end':'2026-01-01'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.context['total'],0)

    def test_full_backup_reserved_for_superuser_and_hidden_for_staff(self):
        self.assertEqual(self.client.post(reverse('backup')).status_code,403)
        self.assertNotContains(self.client.get(reverse('home')),'action="/backup/"')
        self.admin.is_superuser=True; self.admin.save(update_fields=['is_superuser'])
        self.assertContains(self.client.get(reverse('home')),'action="/backup/"')


from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class CompetencyMigrationTests(TransactionTestCase):
    def test_existing_results_preserved_without_inventing_validation_or_competencies(self):
        before=[('training','0017_formation_evaluations')]
        after=[('training','0018_competencies_development_indicators')]
        executor=MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps=executor.loader.project_state(before).apps
            employee=apps.get_model('training','Employee').objects.create(number='M360',name='Migração',function='Função original')
            training=apps.get_model('training','Training').objects.create(employee=employee,title='Histórico',
                start='2026-01-01',end='2026-01-01',days=1,hours_per_day=7,attendance=1,
                status='Concluído',grade=16,result='Aprovado',source_data={'nota_origem':'16'})
            apps.get_model('training','TrainingEvaluation').objects.create(training=training,validation='Validado',source='Fonte antiga')
            executor=MigrationExecutor(connection); executor.migrate(after)
            apps=executor.loader.project_state(after).apps
            row=apps.get_model('training','Training').objects.get(pk=training.pk)
            self.assertEqual(row.grade,16)
            self.assertEqual(row.source_data,{'nota_origem':'16'})
            self.assertEqual(row.employee.function,'Função original')
            evaluation=apps.get_model('training','TrainingEvaluation').objects.get()
            self.assertIsNone(evaluation.validated_grade)
            self.assertEqual(evaluation.validated_result,'')
            self.assertEqual(apps.get_model('training','EvaluationCycle').objects.count(),0)
            self.assertEqual(apps.get_model('training','Competency').objects.count(),0)
        finally:
            executor=MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

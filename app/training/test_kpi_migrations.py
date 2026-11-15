from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

class KPIMigrationTests(TransactionTestCase):
    def test_additive_migration_preserves_legacy_and_reverses_without_new_data(self):
        before=[('training','0024_saved_group_attendance_guide')]
        after=[('training','0027_attendance_import_audit')]
        executor=MigrationExecutor(connection);executor.migrate(before)
        try:
            apps=executor.loader.project_state(before).apps
            employee=apps.get_model('training','Employee').objects.create(number='MIG-KPI',name='Synthetic')
            course=apps.get_model('training','Course').objects.create(title='Synthetic',key='migration')
            group=apps.get_model('training','TrainingGroup').objects.create(course=course,number=1,title='Synthetic',
                trainer='Test',location='Room',hours_per_day=7,start_time='08:00',end_time='16:00',
                start='2025-01-01',end='2025-01-01',days=1)
            session=apps.get_model('training','GroupSession').objects.create(group=group,date='2025-01-01')
            training=apps.get_model('training','Training').objects.create(employee=employee,group=group,course=course,
                title='Legacy',start='2025-01-01',end='2025-01-01',days=1,hours_per_day=7,attendance=1,
                attendance_confirmed=True,grade=16,result='Aprovado')
            evaluation=apps.get_model('training','TrainingEvaluation').objects.create(training=training,validation='Validado')
            executor=MigrationExecutor(connection);executor.migrate(after)
            apps=executor.loader.project_state(after).apps
            self.assertIsNone(apps.get_model('training','TrainingEvaluation').objects.get(pk=evaluation.pk).completion)
            daily=apps.get_model('training','GroupSession').objects.get(pk=session.pk)
            self.assertIsNone(daily.duration);self.assertEqual(daily.code,'principal')
            self.assertFalse(apps.get_model('training','SessionAttendance').objects.exists())
            row=apps.get_model('training','Training').objects.get(pk=training.pk)
            self.assertEqual((row.attendance,row.grade,row.result),(1,16,'Aprovado'))
            executor=MigrationExecutor(connection);executor.migrate(before)
            apps=executor.loader.project_state(before).apps
            self.assertEqual(apps.get_model('training','Training').objects.get(pk=training.pk).attendance,1)
            self.assertTrue(apps.get_model('training','GroupSession').objects.filter(pk=session.pk).exists())
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())

import json,os,tempfile
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command,CommandError
from django.test import TestCase,override_settings
from training import historical,source_achievement
from training.private_data import load
from training.models import Employee,Training,TrainingGroup,Enrollment,GroupSession,Course,PrivateDocument,TrainingDocumentState,AuditEvent

class PrivateDataTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def overrides(self,**kwargs):
        return override_settings(DATA_DIR=self.root,PMF_HISTORICAL_DATA_FILE=None,PMF_ACHIEVEMENT_DATA_FILE=None,**kwargs)
    def test_configured_external_files_and_settings_are_respected(self):
        path=self.root/'history.json';path.write_text(json.dumps({'editions':{},'bindings':{},'excluded_keys':[],'source':'Synthetic'}))
        with self.overrides(),override_settings(PMF_HISTORICAL_DATA_FILE=str(path)):
            self.assertEqual(historical.manifest()['source'],'Synthetic')
    def test_environment_paths_can_be_relative_to_private_data_dir(self):
        path=self.root/'synthetic.json';path.write_text(json.dumps({'items':{'synthetic':{'raw':'Demo'}}}))
        with self.overrides(),patch.dict(os.environ,{'PMF_ACHIEVEMENT_DATA_FILE':'synthetic.json'}):
            self.assertEqual(source_achievement.index()['synthetic']['raw'],'Demo')
    def test_unconfigured_missing_indexes_return_safe_empty_schema(self):
        with self.overrides(),patch.dict(os.environ,{'PMF_HISTORICAL_DATA_FILE':'','PMF_ACHIEVEMENT_DATA_FILE':''}),patch('training.private_data.Path.is_file',return_value=False):
            self.assertEqual(historical.manifest()['bindings'],{})
            self.assertEqual(source_achievement.index(),{})
    def test_configured_missing_or_invalid_file_fails_clearly(self):
        path=self.root/'missing.json'
        with self.overrides(),override_settings(PMF_HISTORICAL_DATA_FILE=str(path)):
            with self.assertRaises(ImproperlyConfigured):historical.manifest()
            path.write_text('{invalid')
            with self.assertRaises(ImproperlyConfigured):historical.manifest()
            path.write_text(json.dumps({'editions':[]}))
            with self.assertRaises(ImproperlyConfigured):historical.manifest()
    def test_runtime_private_location_precedes_legacy_source(self):
        private=self.root/'private';private.mkdir();(private/'pilar_achievement.json').write_text(json.dumps({'items':{'DEMO':{'raw':'Synthetic'}}}))
        with self.overrides():self.assertEqual(source_achievement.index()['DEMO']['raw'],'Synthetic')
    def test_demo_requires_explicit_confirmation_and_never_uses_real_directory(self):
        with self.overrides(PMF_DEMO_MODE=False):
            with self.assertRaises(CommandError):call_command('seed_demo',confirm_demo=True)
        with self.overrides(PMF_DEMO_MODE=True):
            with self.assertRaises(CommandError):call_command('seed_demo')
        with self.overrides(PMF_DEMO_MODE=True),patch.dict(os.environ,{'LOCALAPPDATA':str(self.root)}),override_settings(DATA_DIR=self.root/'PMF'):
            with self.assertRaises(CommandError):call_command('seed_demo',confirm_demo=True)
        self.assertEqual(Employee.objects.count(),0)
    def test_demo_is_synthetic_functional_and_does_not_replace_existing_data(self):
        with self.overrides(PMF_DEMO_MODE=True):
            call_command('seed_demo',confirm_demo=True,verbosity=0)
            rows=historical.records();self.assertEqual(len(rows),6)
            source_achievement.attach([r['training'] for r in rows])
            self.assertEqual(sum(r['training'].evaluation['available'] for r in rows),4)
            self.assertEqual(len(historical.editions(rows)),2)
            self.assertEqual((TrainingGroup.objects.count(),Enrollment.objects.count(),GroupSession.objects.count()),(1,3,2))
            self.assertEqual(set(Employee.objects.values_list('phone',flat=True)),{f'00000000{i}' for i in range(1,9)})
            self.assertEqual(Course.objects.count(),3)
            self.assertEqual(PrivateDocument.objects.count(),2)
            self.assertEqual(TrainingDocumentState.objects.count(),3)
            self.assertEqual(set(Employee.objects.values_list('number',flat=True)),{f'DEMO-{i:03}' for i in range(1,9)})
            for document in PrivateDocument.objects.all():
                from io import BytesIO
                from pypdf import PdfReader
                self.assertIn('DEMONSTRACAO',PdfReader(BytesIO(bytes(document.content))).pages[0].extract_text())
            from training.analytics import statistics
            stats=statistics([r['training'] for r in rows])
            self.assertEqual(stats['numeric_evaluations'],3)
            self.assertEqual(stats['converted_evaluations'],1)
            self.assertEqual(stats['issued_count'],2)
            self.assertEqual(stats['learner_delivery_value'],1)
            self.assertEqual(stats['hr_delivery_count'],1)
            with self.assertRaises(CommandError):call_command('seed_demo',confirm_demo=True)
            self.assertEqual(Training.objects.count(),6)

    def test_demo_never_falls_back_to_local_private_indexes(self):
        with self.overrides(PMF_DEMO_MODE=True):
            self.assertEqual(historical.manifest()['bindings'],{})
            self.assertEqual(source_achievement.index(),{})
            with override_settings(PMF_HISTORICAL_DATA_FILE=str(self.root.parent/'external.json')):
                with self.assertRaises(ImproperlyConfigured):historical.manifest()
            folder=self.root/'private';folder.mkdir()
            (folder/'pilar_achievement.json').write_text(json.dumps({'items':{}}))
            with self.assertRaises(ImproperlyConfigured):source_achievement.index()

    def test_demo_file_failure_rolls_back_database_and_its_own_indexes(self):
        original=json.dump
        calls=[]
        def failing(value,handle,**kwargs):
            calls.append(handle.name)
            if len(calls)==2:raise OSError('Synthetic disk failure')
            return original(value,handle,**kwargs)
        with self.overrides(PMF_DEMO_MODE=True),patch('training.management.commands.seed_demo.json.dump',side_effect=failing):
            with self.assertRaises(OSError):call_command('seed_demo',confirm_demo=True)
        for model in (Employee,Training,Course,TrainingGroup,Enrollment,GroupSession,PrivateDocument,TrainingDocumentState,AuditEvent):
            self.assertEqual(model.objects.count(),0)
        self.assertFalse((self.root/'private'/'historical_editions.json').exists())
        self.assertFalse((self.root/'private'/'pilar_achievement.json').exists())

    def test_demo_rejects_external_sources_without_reading_them(self):
        with self.overrides(PMF_DEMO_MODE=True),override_settings(PMF_ACHIEVEMENT_DATA_FILE='outside.json'):
            with self.assertRaises(CommandError):call_command('seed_demo',confirm_demo=True)
        self.assertEqual(Employee.objects.count(),0)

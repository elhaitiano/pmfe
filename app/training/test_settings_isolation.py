import json,os,pathlib,subprocess,sys,tempfile
from django.test import SimpleTestCase

class SettingsIsolationTests(SimpleTestCase):
    def test_production_settings_honour_explicit_synthetic_directory_and_flags(self):
        with tempfile.TemporaryDirectory(prefix='pmfe-settings-') as directory:
            target=pathlib.Path(directory)/'new-synthetic-data'
            env=dict(os.environ,PMF_DATA_DIR=str(target),PMF_DEMO_MODE='1',PMF_DOCUMENTS_ENABLED='0',PMF_HISTORICAL_ENABLED='0')
            code="import json; from pmf import settings; print(json.dumps({'demo':settings.PMF_DEMO_MODE,'documents':settings.PMF_DOCUMENTS_ENABLED,'history':settings.PMF_HISTORICAL_ENABLED,'data':str(settings.DATA_DIR)}))"
            result=subprocess.run([sys.executable,'-B','-c',code],cwd=pathlib.Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,check=True)
            values=json.loads(result.stdout)
            self.assertTrue(values['demo'])
            self.assertFalse(values['documents'])
            self.assertFalse(values['history'])
            self.assertEqual(pathlib.Path(values['data']),target)
            self.assertTrue((target/'secret.key').is_file())

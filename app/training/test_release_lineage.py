import sqlite3,tempfile,hashlib
from pathlib import Path
from django.test import SimpleTestCase
from django.core.exceptions import ImproperlyConfigured
from pmf.release_lineage import require_compatible_database

class ReleaseLineageTests(SimpleTestCase):
 def test_fresh_path_is_not_created(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'fresh.sqlite3';require_compatible_database(p,Path(__file__).resolve().parent/'migrations');self.assertFalse(p.exists())
 def test_foreign_lineage_is_rejected_without_writes(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'old.sqlite3';c=sqlite3.connect(p);c.execute('CREATE TABLE django_migrations(app text,name text)');c.execute("INSERT INTO django_migrations VALUES('training','0028_certificate_obtained_workforce_reference')");c.commit();c.close();before=hashlib.sha256(p.read_bytes()).hexdigest()
   with self.assertRaises(ImproperlyConfigured):require_compatible_database(p,Path(__file__).resolve().parent/'migrations')
   self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),before)
 def test_8088_lineage_is_accepted(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'current.sqlite3';c=sqlite3.connect(p);c.execute('CREATE TABLE django_migrations(app text,name text)');c.execute("INSERT INTO django_migrations VALUES('training','0028_stabilization_compatibility')");c.commit();c.close();require_compatible_database(p,Path(__file__).resolve().parent/'migrations')

"""Dedicated empty :memory: schema required by the read-only command tests."""
import unittest
from django.conf import settings


if __name__ == '__main__':
    settings.configure(SECRET_KEY='pure-diagnostic-tests',
        INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','training'],
        DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}},
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField', USE_TZ=True)
    import django
    django.setup()
    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromName('training.test_catalogue_diagnostic'))
    raise SystemExit(not result.wasSuccessful())

"""Run synthetic catalogue tests without importing the production settings.

Usage: python -B test_catalogue_isolated.py
Database and test fixtures are disposable; no real Excel or SQLite is opened.
"""
import tempfile
from pathlib import Path


def main():
    from django.conf import settings
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='catalogue-test-', dir=root) as directory:
        previous_tempdir = tempfile.tempdir
        tempfile.tempdir = directory
        try:
            settings.configure(
                SECRET_KEY='synthetic-tests-only', DEBUG=False,
                INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes',
                                'django.contrib.sessions', 'django.contrib.messages', 'training'],
                DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
                DEFAULT_AUTO_FIELD='django.db.models.BigAutoField', ROOT_URLCONF='pmf.urls',
                BASE_DIR=root, DATA_DIR=Path(directory), ALLOWED_HOSTS=['testserver', 'localhost'],
                MIDDLEWARE=['django.contrib.sessions.middleware.SessionMiddleware',
                            'django.middleware.csrf.CsrfViewMiddleware',
                            'django.contrib.auth.middleware.AuthenticationMiddleware',
                            'django.contrib.messages.middleware.MessageMiddleware'],
                TEMPLATES=[{'BACKEND': 'django.template.backends.django.DjangoTemplates',
                            'DIRS': [root / 'templates'], 'APP_DIRS': True,
                            'OPTIONS': {'context_processors': [
                                'django.template.context_processors.request',
                                'django.contrib.auth.context_processors.auth',
                                'django.contrib.messages.context_processors.messages']}}],
                STATIC_URL='/static/', USE_TZ=True, TIME_ZONE='Africa/Luanda',
                LANGUAGE_CODE='pt-pt', LOGIN_URL='login',
                PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
            import django
            django.setup()
            from django.core.management import call_command
            call_command('test', 'training.test_catalogue_phase2', 'training.test_planning',
                         'training.test_course_deletion', 'training.test_duration', 'training.test_duration_batch', 'training.test_catalogue_corrections', 'training.test_correction_extension', 'training.test_catalogue_diagnostic.AnalysisTests',
                         verbosity=1, interactive=False)
            call_command('makemigrations', 'training', check=True, dry_run=True, verbosity=1)
        finally:
            tempfile.tempdir = previous_tempdir


if __name__ == '__main__':
    main()

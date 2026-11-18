"""Synthetic-only settings: never import pmf.settings or the official database."""
import os
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent.parent
DATA_DIR=Path(os.environ['PMFE_TEST_DATA_DIR']).resolve()
SECRET_KEY='synthetic-test-settings-not-for-deployment'
DEBUG=False
ALLOWED_HOSTS=['testserver','localhost','127.0.0.1']
INSTALLED_APPS=['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','training']
DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}}
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
ROOT_URLCONF='pmf.urls'
MIDDLEWARE=['whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','training.interface_sync.InterfaceSyncMiddleware']
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','training.interface_context.archived_documents'],'builtins':['django.templatetags.l10n']}}]
STATIC_URL='/static/'
STATIC_ROOT=BASE_DIR/'static'
WHITENOISE_MIMETYPES={'.ttf':'font/ttf','.woff2':'font/woff2'}
USE_TZ=True
TIME_ZONE='Africa/Luanda'
LANGUAGE_CODE='pt-pt'
FORMAT_MODULE_PATH=['pmf.formats']
LOGIN_URL='/entrar/'
LOGIN_REDIRECT_URL='/'
LOGOUT_REDIRECT_URL='/entrar/'
SESSION_EXPIRE_AT_BROWSER_CLOSE=True
PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']
PMF_DOCUMENTS_ENABLED=True
PMF_HISTORICAL_ENABLED=True
PMF_VALIDATION_ENABLED=False
PMF_CERTIFICATE_SOURCE_ROOT=''
PMF_DEMO_MODE=False

AUTH_PASSWORD_VALIDATORS=[{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]

TEMPLATES[0]['OPTIONS']['context_processors'].append('training.ux_presentation.navigation')

TEMPLATES[0]['OPTIONS']['context_processors'].append('training.completion_presentation.context')

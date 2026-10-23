import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('PMF_DATA_DIR') or (
    Path(os.environ['LOCALAPPDATA']) / 'PMF' if os.environ.get('LOCALAPPDATA') else BASE_DIR / 'data'
))
DATA_DIR.mkdir(parents=True, exist_ok=True)
key_file = DATA_DIR / 'secret.key'
try:
    with key_file.open('x', encoding='utf-8') as handle:
        handle.write(secrets.token_urlsafe(64))
    key_file.chmod(0o600)
except FileExistsError:
    pass
SECRET_KEY = key_file.read_text(encoding='utf-8')
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]']
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.staticfiles', 'django.contrib.auth', 'django.contrib.contenttypes',
                  'django.contrib.sessions', 'django.contrib.messages', 'training']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware',
              'whitenoise.middleware.WhiteNoiseMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware',
              'django.contrib.messages.middleware.MessageMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'pmf.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
              'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': [
                  'django.template.context_processors.request',
                  'django.contrib.auth.context_processors.auth',
                  'django.contrib.messages.context_processors.messages']}}]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                         'NAME': DATA_DIR / 'pmf.sqlite3', 'OPTIONS': {'timeout': 20}}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'pt-pt'
FORMAT_MODULE_PATH = ['pmf.formats']
TIME_ZONE = 'Africa/Luanda'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/entrar/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/entrar/'
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
CSRF_COOKIE_SAMESITE = 'Strict'
X_FRAME_OPTIONS = 'DENY'
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static-collected'
STATICFILES_DIRS = [BASE_DIR / 'static']
WHITENOISE_MIMETYPES = {'.ttf': 'font/ttf', '.woff2': 'font/woff2'}

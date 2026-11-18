import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ISOLATED_ROOT = BASE_DIR.parent.parent
DATA_DIR = Path(os.environ.get('PMFE_ISOLATED_DATA_DIR') or os.environ.get('PMF_DATA_DIR') or BASE_DIR.parent / '.runtime' / 'pmfe-8088').resolve()
if not DATA_DIR.is_relative_to(ISOLATED_ROOT.resolve()):
    raise RuntimeError('Isolated candidate database path is outside its workspace.')
from .release_lineage import require_compatible_database
require_compatible_database(DATA_DIR / 'pmf.sqlite3', BASE_DIR / 'training' / 'migrations')
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

PMF_DOCUMENTS_ENABLED = os.environ.get('PMF_DOCUMENTS_ENABLED', '1') == '1'
PMF_HISTORICAL_ENABLED = os.environ.get('PMF_HISTORICAL_ENABLED', '1') == '1'
PMF_VALIDATION_ENABLED = os.environ.get('PMF_VALIDATION_ENABLED', '0') == '1'
PMF_CERTIFICATE_SOURCE_ROOT = os.environ.get('PMF_CERTIFICATE_SOURCE_ROOT','')
PMF_DEMO_MODE = os.environ.get('PMF_DEMO_MODE', '0') == '1'

MIDDLEWARE.append('training.interface_sync.InterfaceSyncMiddleware')

TEMPLATES[0]['OPTIONS']['context_processors'].append('training.interface_context.archived_documents')

# Visual quantities: narrow, non-breaking spaces between thousands.
USE_THOUSAND_SEPARATOR = True
NUMBER_GROUPING = 3
THOUSAND_SEPARATOR = '\u00a0'
TEMPLATES[0]['OPTIONS'].setdefault('builtins', []).append('django.templatetags.l10n')

# Staff diagnostics are disabled during normal use.
if os.environ.get("PMF_NAVIGATION_DIAGNOSTICS") == "1":
    MIDDLEWARE.append("training.navigation_diagnostics.NavigationDiagnostics")

# Content-versioned icons can be cached without delaying later icon changes.
WHITENOISE_IMMUTABLE_FILE_TEST = r'/pmf/icons/favicon\.[0-9a-f]{12}\.svg$'

TEMPLATES[0]['OPTIONS']['context_processors'].append('training.ux_presentation.navigation')

TEMPLATES[0]['OPTIONS']['context_processors'].append('training.completion_presentation.context')

# Clean-base import: explicit certificates, no historical policy reuse.
PMF_ATTENDANCE_IMPORT_ENABLED = True
PMF_CLEAN_BASE_MODE = True
PMF_VALIDATION_ENABLED = False
PMF_CERTIFICATE_SOURCE_ROOT = ''
PMF_DEMO_MODE = False

SESSION_COOKIE_NAME = 'pmfe_clean_sessionid' if DATA_DIR.name == 'base-limpa' else 'pmfe_test_sessionid'
CSRF_COOKIE_NAME = 'pmfe_clean_csrftoken' if DATA_DIR.name == 'base-limpa' else 'pmfe_test_csrftoken'

# Same-origin internal panels; external framing remains blocked.
X_FRAME_OPTIONS = 'SAMEORIGIN'
TEMPLATES[0]['OPTIONS']['context_processors'].append('training.professional_ui.shell_context')

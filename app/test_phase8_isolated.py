"""All application tests, synthetic SQLite only; never imports pmf.settings."""
from django.core import management
from django.conf import settings
from test_catalogue_isolated import main


if __name__ == '__main__':
    original = management.call_command
    def isolated(command, *args, **kwargs):
        if command == 'test':
            settings.LOGIN_REDIRECT_URL = '/'
            settings.LOGOUT_REDIRECT_URL = '/entrar/'
            settings.SESSION_EXPIRE_AT_BROWSER_CLOSE = True
            settings.STATIC_ROOT = settings.BASE_DIR / 'static'
            settings.WHITENOISE_MIMETYPES = {'.ttf':'font/ttf', '.woff2':'font/woff2'}
            settings.MIDDLEWARE = ['whitenoise.middleware.WhiteNoiseMiddleware', *settings.MIDDLEWARE]
            args = ('training',)
        return original(command, *args, **kwargs)
    management.call_command = isolated
    try:
        main()
    finally:
        management.call_command = original
    # The general runner uses a shared in-memory URI. Exercise the standalone
    # command's stricter empty :memory: schema in a fresh, isolated process.
    import subprocess
    import sys
    from pathlib import Path
    subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('test_diagnostic_memory.py'))], check=True)

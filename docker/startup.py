"""Container orchestration only. The application source remains unchanged."""
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

data = Path(os.environ.get('PMF_DATA_DIR', '/data'))
data.mkdir(parents=True, exist_ok=True)
database = data / 'pmf.sqlite3'
if database.exists():
    # Only one backend is supported; no writer is active yet at startup.
    snapshot = data / 'backups' / datetime.now(timezone.utc).strftime('startup-%Y%m%d-%H%M%S-%f')
    snapshot.mkdir(parents=True)
    with sqlite3.connect(database) as source, sqlite3.connect(snapshot / 'pmf.sqlite3') as destination:
        source.backup(destination)
    if (data / 'reports').exists():
        shutil.copytree(data / 'reports', snapshot / 'reports')
    if (data / 'secret.key').exists():
        shutil.copy2(data / 'secret.key', snapshot / 'secret.key')

subprocess.run(['python', 'manage.py', 'migrate', '--noinput'], cwd='/app', check=True)
os.chdir('/app')
os.execvp('waitress-serve', ['waitress-serve', '--listen=0.0.0.0:8000', '--threads=4', 'pmf.wsgi:application'])

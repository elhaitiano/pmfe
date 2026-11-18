"""Isolated clean base; refuses any path outside this prepared workspace."""
from pathlib import Path
import os
from django.core.exceptions import ImproperlyConfigured
ISOLATED_ROOT=Path(__file__).resolve().parents[3]
isolated_data=Path(os.environ.get('PMFE_ISOLATED_DATA_DIR') or ISOLATED_ROOT/'base-limpa').resolve()
if not isolated_data.is_relative_to(ISOLATED_ROOT.resolve()):raise ImproperlyConfigured('A base de preparação deve permanecer dentro da pasta isolada.')
os.environ['PMF_DATA_DIR']=str(isolated_data)
os.environ['PMF_DEMO_MODE']='0'
os.environ['PMF_CERTIFICATE_SOURCE_ROOT']=''
from .settings import *
PMF_ATTENDANCE_IMPORT_ENABLED=True
PMF_CLEAN_BASE_MODE=True
PMF_HISTORICAL_DATA_FILE=str(ISOLATED_ROOT/'base-limpa/private/historical_editions.json')
PMF_ACHIEVEMENT_DATA_FILE=str(ISOLATED_ROOT/'base-limpa/private/pilar_achievement.json')
ALLOWED_HOSTS=['127.0.0.1','localhost','testserver']

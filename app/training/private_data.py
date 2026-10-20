"""Optional private indexes, outside Git; no real data is required to boot."""
import json,os
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

FILES={'historical':('PMF_HISTORICAL_DATA_FILE','historical_editions.json'),
       'achievement':('PMF_ACHIEVEMENT_DATA_FILE','pilar_achievement.json')}

def demo_context(request):
    return {'pmf_demo_mode':getattr(settings,'PMF_DEMO_MODE',False)}

@lru_cache(maxsize=8)
def _read(path,modified,size):
    try:return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError,ValueError) as exc:
        raise ImproperlyConfigured('Não foi possível ler o índice privado configurado: '+str(path)) from exc

def load(kind):
    variable,name=FILES[kind]
    directory=Path(getattr(settings,'DATA_DIR',Path(settings.BASE_DIR)/'data'))
    configured=getattr(settings,variable,None) or os.environ.get(variable)
    demo=getattr(settings,'PMF_DEMO_MODE',False)
    if configured:
        path=Path(configured).expanduser()
        if not path.is_absolute():path=directory/path
        if demo and path.resolve().parent!=(directory/'private').resolve():
            raise ImproperlyConfigured('DEMONSTRAÇÃO: os índices devem estar na pasta privada da base sintética.')
        if not path.is_file():raise ImproperlyConfigured('Índice privado configurado inexistente: '+str(path))
    else:
        # The ignored legacy location keeps the existing Windows installation
        # working without moving, rewriting or deleting its private files.
        candidates=[directory/'private'/name]
        if not demo:candidates.append(Path(__file__).parent/'data'/name)
        path=next((p for p in candidates if p.is_file()),None)
    if path is None:
        from .analytics import EXCLUDED_KEYS
        return {'schema':1,'editions':{},'bindings':{},'excluded_keys':sorted(EXCLUDED_KEYS),'items':{}}
    stat=path.stat();value=_read(str(path.resolve()),stat.st_mtime_ns,stat.st_size)
    if demo and (not isinstance(value,dict) or value.get('source')!='DEMONSTRAÇÃO SINTÉTICA'):
        raise ImproperlyConfigured('DEMONSTRAÇÃO: índice não identificado como sintético.')
    required=('editions','bindings','excluded_keys') if kind=='historical' else ('items',)
    if not isinstance(value,dict) or any(key not in value for key in required):raise ImproperlyConfigured('Estrutura do índice privado inválida: '+str(path))
    if kind=='historical' and (not isinstance(value['editions'],dict) or not isinstance(value['bindings'],dict) or not isinstance(value['excluded_keys'],list)):raise ImproperlyConfigured('Estrutura histórica inválida: '+str(path))
    if kind=='achievement' and not isinstance(value['items'],dict):raise ImproperlyConfigured('Estrutura de avaliações inválida: '+str(path))
    return value

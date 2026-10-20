"""Copy only application sources/assets from the supplied ZIP; never modify them."""
import argparse
import hashlib
import json
import stat
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
args = parser.parse_args()
target = ROOT / 'app'
if target.exists():
    raise SystemExit('app/ existe déjà. Utilisez un nouveau dossier pour une autre version du ZIP.')
allowed_dirs = {'pmf', 'training', 'templates', 'static'}
allowed_files = {'manage.py', 'requirements.txt'}
manifest = {}
with ZipFile(args.archive) as archive:
    selected = []
    for item in archive.infolist():
        path = PurePosixPath(item.filename)
        if item.is_dir() or not path.parts or path.parts[0] != 'pmf_local':
            continue
        if path.is_absolute() or '..' in path.parts or '\\' in item.filename:
            raise SystemExit('Chemin non sûr dans le ZIP.')
        relative = PurePosixPath(*path.parts[1:])
        if not relative.parts or '__pycache__' in relative.parts or relative.suffix == '.pyc':
            continue
        if relative.parts[0] not in allowed_dirs and str(relative) not in allowed_files:
            continue
        if stat.S_ISLNK(item.external_attr >> 16):
            raise SystemExit('Lien symbolique refusé.')
        if item.file_size > 25 * 1024 * 1024:
            raise SystemExit('Fichier source trop volumineux.')
        if str(relative) in manifest:
            raise SystemExit('Chemin dupliqué dans le ZIP.')
        content = archive.read(item)
        manifest[str(relative)] = hashlib.sha256(content).hexdigest()
        selected.append((relative, content))
    if not {'manage.py', 'requirements.txt', 'pmf/settings.py'} <= set(manifest):
        raise SystemExit('Structure pmf_local attendue introuvable.')
    for relative, content in selected:
        destination = target.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
report = {'archive_sha256': hashlib.sha256(args.archive.read_bytes()).hexdigest(), 'files': manifest}
(ROOT / 'source-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(f'{len(manifest)} fichiers copiés à l’identique dans {target}.')

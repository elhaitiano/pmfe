"""Run the consolidated application locally, without seeding or changing accounts."""
import os,sys,argparse
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8088);args=parser.parse_args()
if not 1024<=args.port<=65535:parser.error('Porta invalida.')
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'app'));os.environ.setdefault('DJANGO_SETTINGS_MODULE','pmf.settings')
from pmf.wsgi import application
from waitress import serve
serve(application,listen='127.0.0.1:'+str(args.port),threads=4)

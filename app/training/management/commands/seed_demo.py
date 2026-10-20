"""Explicit synthetic demo, refused on the real Windows PMF data directory."""
import hashlib,json,os
from pathlib import Path
from datetime import date,time,timedelta
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.db import connection,transaction
from django.db.models import TextField
from django.db.models.functions import Cast
from decimal import Decimal
from training.models import Employee,Training,Course,AuditEvent,TrainingGroup,Enrollment,GroupSession,TrainingDocumentState,PrivateDocument,ExecutiveReport

class Command(BaseCommand):
    help='Cria apenas dados fictícios numa base de desenvolvimento vazia.'
    def add_arguments(self,parser):parser.add_argument('--confirm-demo',action='store_true')
    def handle(self,*args,**options):
        if not getattr(settings,'PMF_DEMO_MODE',False) or not options['confirm_demo']:
            raise CommandError('Exige PMF_DEMO_MODE=1 e --confirm-demo. Nunca utilizar na base real.')
        directory=Path(settings.DATA_DIR).resolve()
        local=os.environ.get('LOCALAPPDATA')
        protected=(Path(local)/'PMF').resolve() if local else None
        database=str(connection.settings_dict['NAME'])
        memory=database==':memory:' or database.startswith('file:memorydb')
        if protected and (directory==protected or protected in directory.parents):raise CommandError('Directório real PMF protegido.')
        if connection.vendor!='sqlite' or not memory and Path(database).resolve()!=(directory/'pmf.sqlite3').resolve():raise CommandError('A base não corresponde ao directório de demonstração.')
        for variable in ('PMF_HISTORICAL_DATA_FILE','PMF_ACHIEVEMENT_DATA_FILE'):
            if getattr(settings,variable,None) or os.environ.get(variable):raise CommandError('Retire caminhos externos antes de criar a demonstração.')
        private=directory/'private'
        paths=[private/'historical_editions.json',private/'pilar_achievement.json']
        if any(p.exists() for p in paths):raise CommandError('Índices existentes serão preservados; escolha um directório vazio.')
        from training.historical import signature
        from training.approved_evaluations import binding,ITEM
        created=[]
        try:
            with transaction.atomic():
                if any(model.objects.exists() for model in (Employee,Training,Course,AuditEvent,TrainingGroup,Enrollment,GroupSession,TrainingDocumentState,PrivateDocument,ExecutiveReport)):
                    raise CommandError('A base contém dados; demonstração recusada para não os substituir.')
                Course.objects.create(title='Formação de demonstração',source_system='epal-proagua-catalogo',source_action_id='DEMO-A',default_days=2,default_hours=7,validation_status='pending')
                manifest={'schema':1,'source':'DEMONSTRAÇÃO SINTÉTICA','excluded_keys':[],'editions':{'DEMO-E':{'id':'DEMO-E','action_id':'DEMO-A','title':'Formação de demonstração','display_id':'DEMO-E','documented_group':'','start':'2026-01-05','end':'2026-01-06','location':'Local fictício','trainer':'Formador fictício','source_origin':'DEMONSTRAÇÃO SINTÉTICA','phase_ids':['DEMO-P'],'phase_details':{'DEMO-P':{'id':'DEMO-P','label':'Fase fictícia','start':'2026-01-05','end':'2026-01-06','days':'2','daily_hours':'7'}}}},'bindings':{}}
                for i in range(1,4):
                    person=Employee.objects.create(number=f'DEMO-{i:03}',name=f'Formando de Demonstração {i}',direction='Direcção fictícia',phone=f'00000000{i}',birth_date=date(1990,1,1),source_data={'Validação Cadastro EPAL':'Definitivo — demonstração sintética','Data de Nascimeto':'1990-01-01','demo':True})
                    t=Training.objects.create(employee=person,title='Formação de demonstração',source_key=f'demo:{i}',start=date(2026,1,5),end=date(2026,1,6),days=2,hours_per_day=7,attendance=2 if i!=3 else 0,attendance_confirmed=i!=3,status='Concluído',grade=16 if i==1 else None,source_data={'demo':True})
                    t=Training.objects.annotate(source_json_text=Cast('source_data',TextField())).get(pk=t.pk)
                    manifest['bindings'][t.source_key]={'edition':'DEMO-E','phase':'DEMO-P','action_id':'DEMO-A','level':'probable','method':'Agrupamento fictício de demonstração','signature':signature(t),'employee_number_sha256':hashlib.sha256(person.number.encode()).hexdigest()}
                    if i==2:
                        AuditEvent.objects.create(actor='demo',action=ITEM,detail=json.dumps({'training_id':t.pk,'source_key':t.source_key,'signature':binding(t,t.source_json_text),'unit':'qualitative','raw':'Bom','percentage':None,'approved':True,'authority':'DEMONSTRAÇÃO SINTÉTICA, sem aprovação EPAL real.'}))
                self.complete_demo(manifest)
                private.mkdir(parents=True,exist_ok=True)
                for path,value in zip(paths,(manifest,{'source':'DEMONSTRAÇÃO SINTÉTICA','items':{}})):
                    with path.open('x',encoding='utf8') as handle:
                        created.append(path)
                        json.dump(value,handle,ensure_ascii=False,indent=2)
        except Exception:
            for path in created:path.unlink()
            raise
        self.stdout.write(self.style.SUCCESS('DEMONSTRAÇÃO: 8 formandos, 3 acções, 6 participações históricas, 2 edições de consulta, 1 turma, 3 inscrições, 2 sessões, 3 estados documentais e 2 PDF fictícios. Sem contas ou palavras-passe.'))

    def complete_demo(self,manifest):
        """Independent examples for every module, exclusively in the empty demo DB."""
        import io
        from reportlab.pdfgen import canvas
        from training.planning import create_group
        from training.historical import signature
        from training.approved_evaluations import binding,ITEM
        source='DEMONSTRAÇÃO SINTÉTICA'
        def create(model,**values):
            item=model(**values);item.full_clean();item.save();return item
        first=Course.objects.get(source_action_id='DEMO-A')
        first.area='Área fictícia A';first.source_metadata={'demo':True,'source':source};first.save()
        course=create(Course,title='Oficina de demonstração',source_system='epal-proagua-catalogo',
            source_action_id='DEMO-B',internal_code='DEMO-B',area='Área fictícia B',default_days=2,
            default_hours=Decimal('7'),validation_status='validated',validation_notes=source+'; sem aprovação institucional.',source_metadata={'demo':True})
        create(Course,title='Acção incompleta de demonstração',source_system='epal-proagua-catalogo',
            source_action_id='DEMO-C',default_days=None,default_hours=None,validation_status='blocked',validation_notes=source)
        people=list(Employee.objects.order_by('number'))
        for i,year in enumerate((1975,1965,1950,None,1992),4):
            birth=date(year,1,1) if year else None
            people.append(create(Employee,number=f'DEMO-{i:03}',name=f'Formando de Demonstração {i}',
                direction=f'Direcção fictícia {i%2+1}',category='Categoria fictícia',function='Função fictícia',
                phone=f'00000000{i}',email=f'demo{i}@example.invalid',birth_date=birth,
                source_data={'demo':True,'Validação Cadastro EPAL':'Definitivo — demonstração sintética','Data de Nascimeto':birth.isoformat() if birth else ''}))
        for person,year in zip(people[:3],(2005,1995,1985)):
            person.birth_date=date(year,1,1);person.category='Categoria fictícia';person.function='Função fictícia'
            person.source_data['Data de Nascimeto']=person.birth_date.isoformat();person.save()
        edition={**manifest['editions']['DEMO-E'],'id':'DEMO-E2','action_id':'DEMO-B',
            'title':course.title,'display_id':'DEMO-E2','documented_group':'DEMO-E2'}
        manifest['editions']['DEMO-E2']=edition
        for i,person in enumerate(people[3:6],4):
            t=create(Training,employee=person,title=course.title,source_key=f'demo:{i}',
                start=date(2026,1,5),end=date(2026,1,6),days=2,hours_per_day=Decimal('7'),
                attendance=2 if i!=6 else 0,attendance_confirmed=i!=6,status='Concluído',
                grade=Decimal('0') if i==4 else None,source_data={'demo':True},observations=source)
            t=Training.objects.annotate(source_json_text=Cast('source_data',TextField())).get(pk=t.pk)
            manifest['bindings'][t.source_key]={'edition':'DEMO-E2','phase':'DEMO-P','action_id':'DEMO-B',
                'level':'confirmed','method':source,'signature':signature(t),'employee_number_sha256':hashlib.sha256(person.number.encode()).hexdigest()}
            if i==5:
                AuditEvent.objects.create(actor='DEMO',action=ITEM,detail=json.dumps({'training_id':t.pk,'source_key':t.source_key,
                    'signature':binding(t,t.source_json_text),'unit':'percentage','raw':'80%','percentage':'80',
                    'approved':True,'authority':source,'corrected':True,'scope':'final_achievement','demo':True}))
        # Signatures bind the final synthetic values, never institutional rows.
        for t in Training.objects.annotate(source_json_text=Cast('source_data',TextField())):
            manifest['bindings'][t.source_key]['signature']=signature(t)
        for i,t in enumerate(Training.objects.order_by('pk')[:3]):
            create(TrainingDocumentState,training=t,issued=i!=2,learner_delivered=True if i==0 else False if i==1 else None,
                learner_date=date(2026,2,1) if i==0 else None,learner_responsible='Responsável fictício' if i==0 else '',
                hr_delivered=True if i==1 else None,hr_date=date(2026,2,2) if i==1 else None,
                hr_responsible='Responsável fictício' if i==1 else '',reference=source,observations='Estados fictícios, sem validade institucional.')
        start=date.today()+timedelta(days=30)
        group=create_group(course,dict(dates=[start,start+timedelta(days=2)],trainer='Formador fictício',provider=source,
            location='Sala de demonstração',capacity=10,hours_per_day=Decimal('7'),break_hours=Decimal('1'),start_time=time(8),end_time=time(16)))
        for person in people[:3]:create(Enrollment,employee=person,group=group,contact_note=source)
        for kind in ('cv','certificate'):
            stream=io.BytesIO();pdf=canvas.Canvas(stream)
            pdf.setTitle('DEMONSTRACAO - documento ficticio')
            pdf.drawString(45,780,'DEMONSTRACAO - DOCUMENTO FICTICIO - SEM VALIDADE')
            pdf.drawString(45,750,'Formando de Demonstracao 1 - exemplo sintetico independente');pdf.save()
            content=stream.getvalue()
            create(PrivateDocument,employee=people[0],training=Training.objects.get(source_key='demo:1') if kind=='certificate' else None,
                kind=kind,content=content,size=len(content),sha256=hashlib.sha256(content).hexdigest(),actor='DEMO',reference=source)
        AuditEvent.objects.create(actor='DEMO',action='DEMO_SEED',detail=json.dumps({'demo':True,'source':source,
            'employees':8,'historical':6,'courses':3,'groups':1,'enrollments':3,'sessions':2,'documents':2}))

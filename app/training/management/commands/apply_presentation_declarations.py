import json
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.db.models import F
from training.models import Training,AuditEvent
from training.scheduling_guard import reservation_transaction

class Command(BaseCommand):
 def add_arguments(self,p):p.add_argument('manifest');p.add_argument('--apply',action='store_true')
 def handle(self,*args,**opts):
  m=json.loads(Path(opts['manifest']).read_text());assert m.get('demo') is True
  with reservation_transaction():
   plans=[];seen=set();certs=0;existing=0
   for item in m['records']:
    if item['id'] in seen:raise CommandError('Duplicate participation')
    seen.add(item['id']);t=Training.objects.select_related('course','employee').get(pk=item['id'])
    if [t.source_key,t.employee.number,t.course.code,t.start.isoformat(),t.end.isoformat(),t.status,t.grade,t.result]!=[item[k] for k in ['source_key','number','course_code','start','end','status','grade','result']]:raise CommandError('Identity or historical values changed')
    data=dict(t.source_data or {});new=dict(data)
    for key,val in [('_presentation_grade',item['presentation']),('_certificate_delivery_declaration',item['certificate_declaration'])]:
     if val is None:continue
     if key in data and data[key]!=val:raise CommandError('Refusing to replace existing declaration or simulation')
     new[key]=val
    t.source_data=new
    if t.simulated_grade is None:raise CommandError('Invalid simulated grade')
    if item['certificate_declaration'] and not t.declared_delivery_date:raise CommandError('Ineligible certificate declaration')
    certs+=bool(item['certificate_declaration'])
    if data==new:existing+=1
    else:plans.append((t,new))
   if opts['apply']:
    for t,data in plans:
     if Training.objects.filter(pk=t.pk,version=t.version).update(source_data=data,version=F('version')+1)!=1:raise CommandError('Concurrent modification')
    if plans:AuditEvent.objects.create(actor='Responsavel pelo projecto',action='DEMO_GRADES_DELIVERY_DECLARATION',detail=json.dumps({'participations':len(plans),'declared_deliveries':certs,'documentary_validation':False,'historical_values_preserved':True}))
   else:transaction.set_rollback(True)
  self.stdout.write(json.dumps({'participations':len(seen),'updated':len(plans) if opts['apply'] else 0,'missing':len(plans) if not opts['apply'] else 0,'existing':existing,'declared_deliveries':certs,'unmatched':0}))

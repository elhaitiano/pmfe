import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db.models import F
from training.models import Training, AuditEvent
from training.scheduling_guard import reservation_transaction
from training.original_assessments import percentage

class Command(BaseCommand):
    help='Import exact, reconciled demonstration assessments without changing academic grades/results.'
    def add_arguments(self,parser):
        parser.add_argument('manifest')
        parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        manifest=json.loads(Path(options['manifest']).read_text())
        if manifest.get('identification')!='AMBIENTE_DE_DEMONSTRACAO':raise CommandError('Demonstration manifest required.')
        items=manifest['records'];seen=set();plans=[];existing=0
        with reservation_transaction():
            for item in items:
                pk=item['training_id']
                if pk in seen:raise CommandError('Duplicate participation in manifest.')
                seen.add(pk)
                row=Training.objects.select_related('employee','course').get(pk=pk)
                if (row.source_key!=item['source_key'] or row.employee.number!=item['employee_number'] or
                    not row.course_id or row.course.code!=item['course_code'] or
                    row.start.isoformat()!=item['start'] or row.end.isoformat()!=item['end'] or
                    row.grade!=item['expected_grade'] or row.result!=item['expected_result']):
                    raise CommandError('Participation identity or assessment changed; nothing imported.')
                assessment=item['assessment']
                if assessment.get('source_sha256')!=manifest['source']['sha256'] or not assessment.get('demonstration'):
                    raise CommandError('Invalid source provenance.')
                if assessment.get('kind')=='percentage' and percentage(assessment) is None:raise CommandError('Invalid original percentage.')
                old=row.original_assessment
                if old:
                    if old!=assessment:raise CommandError('Existing original assessment differs; refusing replacement.')
                    existing+=1;continue
                plans.append((row,assessment))
            if options['apply']:
                for row,assessment in plans:
                    payload=dict(row.source_data or {});payload['_original_assessment']=assessment
                    if Training.objects.filter(pk=row.pk,version=row.version).update(source_data=payload,version=F('version')+1)!=1:
                        raise CommandError('Participation changed; import rolled back.')
                if plans:AuditEvent.objects.create(actor='AMBIENTE_DE_DEMONSTRACAO',action='IMPORT_ORIGINAL_ASSESSMENTS',detail=json.dumps({'imported':len(plans),'source_sha256':manifest['source']['sha256'],'grades_and_results_unchanged':True}))
            else:
                # Read-only dry-run, including the reservation counter.
                from django.db import transaction
                transaction.set_rollback(True)
        self.stdout.write(json.dumps({'source_assessments':len(items),'imported':len(plans) if options['apply'] else 0,'missing':0 if options['apply'] else len(plans),'existing':existing,'pending_matching':0,'applied':options['apply']}))

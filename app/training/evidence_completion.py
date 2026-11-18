"""Apply a hash-verified documentary supplement without filling unknown facts."""
import copy
import hashlib
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from .documentary_import import digest
from .certificate_evidence import io_path, normalize
from .models import (AuditEvent, Course, Employee, Training, ConsolidatedEdition,
    ConsolidatedParticipation, HistoricalInterfaceLink, HistoricalReviewSnapshot,
    HistoricalCertificatePDF, DocumentaryRecord, DocumentaryDecision, TrainingDocumentState)
from . import historical_regularization as history
from .historical_review import latest_snapshot, active_pdfs

ACTOR = 'Regularização documental EPAL 06/11/2026'

def event(action, data):
    AuditEvent.objects.create(actor=ACTOR,action=action,detail=json.dumps(data,ensure_ascii=False,default=str))

def same_grade(a,b):
    return Decimal(str(a['value']))*Decimal(str(b['scale'])) == Decimal(str(b['value']))*Decimal(str(a['scale']))

def documented_issue_date(end, documents):
    """Never pick one issue date from conflicting versions, or infer delivery."""
    dates={d.get('facts',{}).get('issue_date') for d in documents}
    dates.discard(None);dates.discard('')
    if len(dates)!=1:return None
    try:value=date.fromisoformat(next(iter(dates)))
    except (ValueError,TypeError):return None
    return value if end<=value<=history.CUTOFF else None

def apply(plan, source_root, issued_proofs=()):
    """The caller backs up the database before calling this atomic operation."""
    key='EPAL_SUPPLEMENT_'+digest([plan,list(issued_proofs)])
    existing=HistoricalReviewSnapshot.objects.filter(key=key).first()
    if existing:return existing.report['incremental_regularization']
    root=Path(source_root).resolve()
    # Recheck sources immediately before entering the transaction.
    for relative,sha in plan['files'].items():
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(io_path(path).read_bytes()).hexdigest()!=sha:
            raise ValidationError('Fonte alterada ou fora da pasta autorizada: '+relative)
    raw_pdfs={}
    for cert in plan.get('certificates',[]):
        path=Path(cert['read_path']).resolve()
        allowed=Path(__file__).resolve().parents[3]/'entregaveis'
        if not (path.is_relative_to(root) or path.is_relative_to(allowed)):
            raise ValidationError('PDF extraído fora da pasta autorizada.')
        data=io_path(path).read_bytes()
        if hashlib.sha256(data).hexdigest()!=cert['document']:raise ValidationError('PDF alterado.')
        from .documents import validate_pdf
        from django.core.files.uploadedfile import SimpleUploadedFile
        validate_pdf(SimpleUploadedFile('certificado.pdf',data,content_type='application/pdf'))
        raw_pdfs[cert['document']]=data
    with transaction.atomic():
        return _apply(plan,key,issued_proofs,raw_pdfs)

def _apply(plan,key,issued_proofs,raw_pdfs):
    counts=Counter();changes=[];changed=set();editions={}
    previous=latest_snapshot();report=copy.deepcopy(previous.report) if previous else {}
    report.setdefault('context_sources',{})
    report.setdefault('certificate_sources',[])
    for item in plan.get('course_area_updates',[]):
        from .training_areas import AREAS
        from .models import TrainingAreaChange,TrainingAreaIssue
        from django.utils import timezone
        if item['area'] not in AREAS:raise ValidationError('Área fora das quatro categorias.')
        course=Course.objects.get(pk=item['course_id']);old=course.area
        if old!=item['area']:
            course.area=item['area'];course.save(update_fields=['area'])
            TrainingAreaChange.objects.get_or_create(policy=plan['rule'],model_name='Course',object_id=str(course.pk),defaults={
                'previous_area':old,'new_area':course.area,'reason':item['reason']})
            TrainingAreaIssue.objects.filter(model_name='Course',object_id=str(course.pk)).update(resolved_at=timezone.now())
            changed.update(ConsolidatedParticipation.objects.filter(active=True,edition__course=course).values_list('pk',flat=True))
            event('TRAINING_AREA_DOCUMENTED',{'course_id':course.pk,'before':old,'after':course.area,'evidence':item})
            counts['course_areas_documented']+=1
    def edition(spec):
        signature=digest(spec)
        if signature in editions:return editions[signature]
        e=ConsolidatedEdition.objects.get(pk=spec['existing_id']) if spec['existing_id'] else None
        if e and (e.course_id!=spec['course_id'] or str(e.start)!=spec['start'] or str(e.end)!=spec['end']):
            raise ValidationError('A edição mudou desde a preparação.')
        if e and e.group_label not in ('',spec['group']):raise ValidationError('O grupo mudou desde a preparação.')
        if e is None:
            e,created=ConsolidatedEdition.objects.get_or_create(source_key='evidence:'+digest([spec['course_id'],spec['start'],spec['end'],spec['group']]),defaults={
                'course_id':spec['course_id'],'title':Course.objects.get(pk=spec['course_id']).title,'course_reference':spec['code'],
                'start':spec['start'],'end':spec['end'],'group_label':spec['group'],'active':True,'evidence':{'origin':plan['rule']}})
            counts['editions_created']+=created
        before={f:str(getattr(e,f)) for f in ['course_reference','group_label','declared_days','hours_per_day','total_hours']}
        if e.course_reference.startswith('TITLE:') and not spec['code'].startswith('TITLE:'):
            e.evidence={**e.evidence,'canonical_reference_decision':{'before':e.course_reference,'after':spec['code'],'rule':plan['rule']}}
            e.course_reference=spec['code']
        if not e.group_label:e.group_label=spec['group']
        if spec.get('days') and e.declared_days is None:e.declared_days=Decimal(spec['days'])
        if spec.get('hours') and not spec.get('hours_conflict'):
            value=Decimal(spec['hours'])
            if e.hours_per_day is None:e.hours_per_day=value
            if e.total_hours is None and e.declared_days is not None:e.total_hours=e.declared_days*value
        for field in ['location','trainer']:
            if not getattr(e,field) and spec.get(field):setattr(e,field,spec[field])
        if spec.get('hours_conflict') and not e.evidence.get('responsible_hours_confirmation'):
            e.evidence={**e.evidence,'hours_divergence_20261106':spec['hours_conflict']}
            sources=[r['source'] for r in plan['rows'] if r.get('attendance') and r['edition']==spec]
            if sources:
                context='supplement-hours-'+digest([e.pk,spec['hours_conflict']])[:20]
                payload={'Contexto documental':context,'Referência da ação na documentação':e.course_reference,'Título literal':e.title,
                    'Início documentado':spec['start'],'Fim documentado':spec['end'],'Grupo de origem':spec['group'],
                    'Dias explicitamente declarados':[1],'Horas/dia explicitamente declaradas':[], 'divergence':spec['hours_conflict'],
                    'claims':[{'source':sources[0],'value':7,'scale':'horas','eligible_documentary_assertion':True}]}
                source_record('context',None,payload,'CONFLITO')
                e.evidence['hours_divergence_context']=context
                report['context_sources'][context]=sources[:1]
        e.save()
        after={f:str(getattr(e,f)) for f in before}
        if before!=after:
            changed.update(e.participations.filter(active=True).values_list('pk',flat=True))
            event('DOCUMENTED_EDITION_FIELDS',{'edition_id':e.pk,'before':before,'after':after,'rule':plan['rule']})
            counts['edition_metadata_updated']+=1
        if e.group_label.isdigit():
            c=e.course
            if c and c.next_group<=int(e.group_label):c.next_group=int(e.group_label)+1;c.save(update_fields=['next_group'])
        editions[signature]=e
        return e
    def participant(number,spec):
        employee=Employee.objects.get(number=number);e=edition(spec)
        rows=list(ConsolidatedParticipation.objects.filter(employee=employee,edition=e,active=True))
        if len(rows)>1:raise ValidationError('Participação duplicada; suplemento suspenso.')
        if rows:return rows[0]
        unassigned=list(ConsolidatedParticipation.objects.filter(employee=employee,active=True,edition__course_id=e.course_id,
            edition__start=e.start,edition__end=e.end,edition__group_label='').exclude(edition=e))
        if len(unassigned)==1 and e.group_label:
            p=unassigned[0];old=p.edition_id;p.edition=e
            p.evidence.setdefault('edition_association_decisions',[]).append({'previous_edition_id':old,'edition_id':e.pk,'rule':plan['rule'],
                'reason':'Mesmo funcionário, acção e período; grupo agora explicitado na fonte nominal, sem duplicar a participação.'})
            p.save(update_fields=['edition','evidence']);counts['existing_participations_group_identified']+=1
            return p
        p,created=ConsolidatedParticipation.objects.get_or_create(source_key=digest(['evidence',employee.pk,e.source_key]),defaults={
            'employee':employee,'edition':e,'active':True,'evidence':{'origin':plan['rule']}})
        counts['participations_created']+=created
        return p
    def source_record(kind,employee,payload,state='COMPROVADO'):
        record,created=DocumentaryRecord.objects.get_or_create(source_key='supplement:'+digest([kind,employee.pk if employee else None,payload]),defaults={
            'kind':kind,'employee':employee,'state':state,'payload':payload,'payload_sha256':digest(payload),'package_sha256':digest(plan)})
        return record,created
    for row in plan['rows']:
        p=participant(row['number'],row['edition']);before=copy.deepcopy({f:getattr(p,f) for f in ['present_count','attendance_complete','attendance_percent','attended_hours','grades']})
        source=row['source'];sources=p.evidence.setdefault('supplement_sources',[])
        if source not in sources:sources.append(source)
        for grade in row.get('grades',[]):
            value,scale=Decimal(grade['value']),Decimal(grade['scale'])
            if not value.is_finite() or not scale.is_finite() or scale<=0 or not 0<=value<=scale:raise ValidationError('Nota fora da escala.')
            old=[g for g in p.grades if g['stage']==grade['stage']]
            if old and not all(same_grade(g,grade) for g in old):
                payload={'number':row['number'],'course_reference':p.edition.course_reference,'start_declared':str(p.edition.start),'end_declared':str(p.edition.end),
                    'state':'CONFLITO','claims':[{'value':g['value'],'scale':g['scale'],'eligible_documentary_assertion':True,'source':{'source':'Facto canónico anteriormente comprovado','participation_id':p.pk}} for g in old]+[{'value':grade['value'],'scale':grade['scale'],'eligible_documentary_assertion':True,'source':source}]}
                _,created=source_record('held_assertion',p.employee,payload,'CONFLITO');counts['grade_conflicts_held']+=created
                continue
            if not old:p.grades.append(grade);counts['grades_added']+=1
            else:
                claims=old[0].setdefault('claims',[])
                for claim in grade.get('claims',[]):
                    if claim not in claims:claims.append(claim);counts['grade_sources_corroborated']+=1
            payload={'number':row['number'],'course_reference':p.edition.course_reference,'date':str(p.edition.end),'stage':grade['stage'],
                'value':grade['value'],'scale':grade['scale'],'claims':[{'source':source,'value':grade['value'],'scale':grade['scale']}],'participation_id':p.pk}
            source_record('original_test_reviewed',p.employee,payload)
        mark=row.get('attendance')
        if mark and p.present_count not in (None,1):
            source_record('held_assertion',p.employee,{'tipo':'presença','number':row['number'],'course_reference':p.edition.course_reference,
                'date':mark['date'],'claims':[{'value':p.present_count,'scale':'1','eligible_documentary_assertion':True,
                'source':{'source':'Contagem anteriormente comprovada','participation_id':p.pk}},
                {'value':1,'scale':'1','eligible_documentary_assertion':True,'source':source}]},'CONFLITO')
            counts['attendance_conflicts_held']+=1;mark=None
        if mark:
            # These supplements are single-day lists only, not implied multi-day coverage.
            if p.edition.start!=p.edition.end or str(p.edition.start)!=mark['date']:raise ValidationError('Lista diária não corresponde à edição.')
            payload={'number':row['number'],'course_reference':p.edition.course_reference,'date':mark['date'],'state':'PRESENTE_DECLARADO',
                'claims':[{'source':source,'status':'PRESENTE_DECLARADO','eligible_documentary_assertion':True}], 'partial_session':mark['partial_session'],'participation_id':p.pk}
            source_record('attendance_assertion',p.employee,payload,'PRESENTE_DECLARADO')
            if p.present_count is None:p.present_count=1
            if mark['complete'] and not p.edition.evidence.get('hours_divergence_20261106'):
                p.attendance_complete=True;p.absent_count=0;p.attendance_percent=Decimal(100)
                hours=mark.get('hours') or p.edition.evidence.get('responsible_hours_confirmation',{}).get('hours')
                if hours and p.attended_hours is None:p.attended_hours=Decimal(hours)
            elif mark['complete']:
                p.attendance_complete=True;p.absent_count=0;p.attendance_percent=Decimal(100)
            if mark.get('partial_session'):
                detail={'source':source,'arrival':mark.get('arrival'),'hours_not_inferred':True}
                if detail not in p.evidence.setdefault('partial_attendance',[]):p.evidence['partial_attendance'].append(detail)
                source_record('held_assertion',p.employee,{'tipo':'presença','number':row['number'],'course_reference':p.edition.course_reference,
                    'date':mark['date'],'claims':[{'source':source}],'reason':'Chegada posterior ao início; duração individual não comprovada.','participation_id':p.pk},'PRESENCA_PARCIAL_HORAS_NAO_COMPROVADAS')
            counts['daily_attendance_evidence']+=1
        p.save()
        after={f:getattr(p,f) for f in before}
        if before!=after or source in sources:
            changed.add(p.pk);changes.append({'participation_id':p.pk,'number':p.employee.number,'before':before,'after':after})
    for e in {e.pk:e for e in editions.values()}.values():
        context=e.evidence.get('hours_divergence_context')
        if not context:continue
        for member in e.participations.filter(active=True).select_related('employee'):
            source_record('nominal',member.employee,{'number':member.employee.number,'event_id':context,
                'scope':'Coorte da edição; não confirma presença, duração individual ou resultado.',
                'sources':member.evidence.get('supplement_sources',[])})
    certificates=[]
    for cert in plan.get('certificates',[]):
        p=participant(cert['number'],cert['edition']);f=cert['facts']
        if not f.get('certificate_front') or not f.get('completion_literal') or not f.get('issue_date') or f['issue_date']>history.CUTOFF.isoformat() or f['course_period']!=[str(p.edition.start),str(p.edition.end)]:
            raise ValidationError('Certificado fora da regra histórica ou do período da participação.')
        p.completed=True
        p.evidence.setdefault('supplement_certificates',[])
        if cert['document'] not in p.evidence['supplement_certificates']:p.evidence['supplement_certificates'].append(cert['document'])
        p.save();changed.add(p.pk)
        e=p.edition
        if e.total_hours is None and f.get('total_hours'):e.total_hours=Decimal(f['total_hours']);e.save(update_fields=['total_hours'])
        payload={'number':cert['number'],'document':cert['document'],'course_literal':e.title,'sources':[cert['source']],
            'state':'CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA','identity_evidence':cert['identity_evidence']}
        record,_=source_record('certificate_reference',p.employee,payload,'CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA')
        certificates.append((p.pk,record.pk,cert))
        if not any(x['document']==cert['document'] for x in report['certificate_sources']):
            report['certificate_sources'].append({'document':cert['document'],'number':cert['number'],'facts':f,'read_path':cert['read_path'],
                'sources':[cert['source']],'course':e.title,'course_codes':[e.course_reference],'quality_flags':[]})
    # Register metadata corroboration without rewriting the raw context payload.
    for item in plan['contexts']:
        r=DocumentaryRecord.objects.get(pk=item['record_id']);prior=history.latest_decision(r)
        if prior and prior.resolution.get('state')=='resolved':continue
        values=item['confirmed_values'];resolution={'state':'partial','confirmed_values':values,'edition_id':item['edition_id'],'automatic_rule':plan['rule'],
            'scope':'Metadados da edição já comprovada, mesmo período, acção e coorte; sem confirmar notas ou presenças.'}
        if prior and prior.resolution==resolution:continue
        d=DocumentaryDecision.objects.create(record=r,actor=ACTOR,reason='Campo já comprovado na consolidação; regularização do contexto de origem sem nova confirmação individual.',
            evidence=json.dumps({'edition_id':item['edition_id'],'sources':item['sources']},ensure_ascii=False),rule_signature=history.rule_signature(r),resolution=resolution)
        if not history.missing_context_fields(r):d.resolution['state']='resolved';d.save(update_fields=['resolution']);counts['contexts_resolved']+=1
        else:counts['contexts_partially_resolved']+=1
        event('CONTEXT_CONFIRMED_FROM_CONSOLIDATION',{'record_id':r.pk,'decision_id':d.pk,'resolution':d.resolution})
    # Project only touched participations, preserving unrelated forms and manual edits.
    from .historical_interface import install
    if changed:install(sorted(changed))
    touched=set(changed)
    certificate_proofs={p['document']:p for p in report.get('certificate_sources',[])}
    for pk,record_id,cert in certificates:
        t=HistoricalInterfaceLink.objects.select_related('training').get(participation_id=pk).training
        other=list(active_pdfs().filter(sha256=cert['document']))
        if other and not any(d.training_id==t.pk for d in other):raise ValidationError('PDF já associado a outra participação; não se duplicou.')
        pdf,created=HistoricalCertificatePDF.objects.get_or_create(training=t,sha256=cert['document'],defaults={'source_record_id':record_id,
            'content':raw_pdfs[cert['document']],'source_reference':cert['source'],'binding_evidence':{'employee_number':cert['number'],
                'participation_id':pk,'identity_evidence':cert['identity_evidence'],'review_state':'PERIODO_CONCORDANTE','method':plan['rule']}})
        counts['pdfs_archived']+=created
        state,_=TrainingDocumentState.objects.get_or_create(training=t,defaults={'reference':cert['source'][:500]})
        if state.issued is False:raise ValidationError('PDF contradiz emissão explicitamente negada.')
        state.issued=True;state.save(update_fields=['issued'])
        counts['historical_deliveries_confirmed']+=history.confirm_delivery(t,ACTOR,'PDF final anterior ao limite histórico; '+cert['document'])
        t.certified=True;t.certificate_issued=True
        if not t.certificate_issue_date:t.certificate_issue_date=date.fromisoformat(cert['facts']['issue_date'])
        t.version+=1
        facts=t.source_data['_EPAL_CONSOLIDATED_FACTS'];facts['historical_delivery_confirmed']=True
        facts['archived_pdf_ids']=sorted(set(facts.get('archived_pdf_ids',[]))|{pdf.pk})
        from .interface_sync import inputs
        facts['inputs']=inputs(t);facts['sync_version']=t.version
        t.save(update_fields=['certified','certificate_issued','certificate_issue_date','version','source_data'])
        event('HISTORICAL_PDF_ARCHIVED',{'archive_id':pdf.pk,'training_id':t.pk,'participation_id':pk,'sha256':pdf.sha256,'binding':pdf.binding_evidence})
    for proof in issued_proofs:
        t=Training.objects.select_for_update().get(pk=proof['training_id'])
        if t.certificate_issued is True:continue
        if t.certificate_issued is False or t.status!='Concluído':raise ValidationError('Emissão contraditória: revisão obrigatória.')
        pdfs=list(active_pdfs().filter(pk__in=proof['pdf_ids']))
        if len(pdfs)!=len(set(proof['pdf_ids'])):raise ValidationError('PDF deixou de estar validado.')
        allowed_ids=set(t.source_data.get('_EPAL_CONSOLIDATED_FACTS',{}).get('archived_pdf_ids',[]))
        if not set(proof['pdf_ids'])<=allowed_ids:raise ValidationError('PDF não pertence à ficha desta participação.')
        for pdf in pdfs:
            if pdf.source_record.employee_id!=t.employee_id:raise ValidationError('PDF pertence a outro formando.')
            if hashlib.sha256(bytes(pdf.content)).hexdigest()!=pdf.sha256:raise ValidationError('Conteúdo arquivado alterado.')
        t.certificate_issued=True;t.version+=1
        issue_date=documented_issue_date(t.end,[certificate_proofs.get(p.sha256,{}) for p in pdfs])
        if t.certificate_issue_date is None and issue_date is not None:
            t.certificate_issue_date=issue_date;counts['documented_issue_dates_recovered']+=1
        facts=t.source_data.get('_EPAL_CONSOLIDATED_FACTS',{})
        from .interface_sync import inputs
        facts['inputs']=inputs(t);facts['sync_version']=t.version;t.source_data['_EPAL_CONSOLIDATED_FACTS']=facts
        t.save(update_fields=['certificate_issued','certificate_issue_date','version','source_data'])
        event('CERTIFICATE_ISSUANCE_FROM_ARCHIVED_FINAL',{'training_id':t.pk,'pdf_ids':proof['pdf_ids'],'before':None,'after':True,'delivery_unchanged':True,
            'issue_date':t.certificate_issue_date,'issue_date_basis':'Data explícita e concordante nos PDFs finais, sem inferir entrega'})
        counts['issuance_flags_regularized']+=1
    for change in changes:event('DOCUMENTED_PARTICIPATION_SUPPLEMENT',change)
    # Show only possible EPAL identities in the review queue. No worker is
    # created for a name outside the confirmed official roster.
    roster=list(Employee.objects.all())
    for item in plan['unresolved']:
        source=item.get('source',{})
        if not isinstance(source,dict) or not source.get('document'):continue
        employee=next((e for e in roster if e.number==item.get('number')),None)
        name=item.get('name') or ' '.join(item.get('names',[]))
        tokens={v for v in normalize(name).split() if len(v)>1 and v not in {'de','da','do','dos','das'}}
        candidates=[e.number for e in roster if len(tokens)>=2 and tokens<=set(normalize(e.name).split())]
        if not employee and not candidates:counts['unmatched_names_kept_outside_roster']+=1;continue
        payload={'source':source['source'],'document':source['document'],'claims':[{'source':source}],
            'name_literal':name,'course_reference':item.get('code'),'candidate_numbers':candidates,'reason':item['reason']}
        if 'rubrica' in item['reason'].lower():payload.update(tipo='presença',date=item.get('date'))
        record,created=source_record('held_assertion' if employee else 'unlinked_test',employee,payload,'EVIDENCIA_OU_IDENTIDADE_POR_COMPROVAR')
        item['record_id']=record.pk;counts['new_review_items']+=created
    result={'rule':plan['rule'],'counts':dict(counts),'unresolved':plan['unresolved'],'source_files':plan['files'],'changed_participation_ids':sorted(touched),
        'delivery_rule_preserved':history.RULE,'no_assumed_values':True}
    report['incremental_regularization']=result
    HistoricalReviewSnapshot.objects.create(key=key,actor=ACTOR,report=report)
    event('DOCUMENTARY_SUPPLEMENT_APPLIED',{'key':key,'counts':dict(counts)})
    return result

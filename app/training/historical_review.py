"""Reconcile saved evidence without replacing delivery declarations or raw claims."""
import hashlib
import json
from collections import defaultdict, Counter
from decimal import Decimal
from pathlib import Path
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from .models import (DocumentaryRecord, DocumentaryDecision, HistoricalCertificatePDF,
    HistoricalReviewSnapshot, Training, TrainingDocumentState, AuditEvent, Employee)
from .models import HistoricalDocumentaryParticipation, HistoricalInterfaceLink
from . import historical_regularization as history
from .certificate_evidence import certificate_facts, normalize, io_path
from .documentary_import import digest
from .analytics import EXCLUDED_KEYS

KEY = 'EPAL_REVIEW_20261105_V1'


def active_pdfs():
    return HistoricalCertificatePDF.objects.filter(Q(binding_evidence__review_state__isnull=True)|Q(binding_evidence__review_state='PERIODO_CONCORDANTE'))


def latest_snapshot():
    return HistoricalReviewSnapshot.objects.order_by('-pk').first()


def audit(action, payload):
    AuditEvent.objects.create(actor='Conferência documental automática EPAL', action=action,
        detail=json.dumps(payload, ensure_ascii=False, default=str))


def apply_attendance_and_context_hours():
    contexts=list(DocumentaryRecord.objects.filter(kind='context'))
    context_by_id={c.payload.get('Contexto documental'):c for c in contexts}
    members=defaultdict(set)
    for r in DocumentaryRecord.objects.filter(kind='nominal'):
        members[r.payload.get('event_id')].add(r.employee_id)
    operational=list(Training.objects.filter(end__lte=history.CUTOFF).exclude(source_key__in=EXCLUDED_KEYS).select_related('employee'))
    index=defaultdict(list)
    for t in operational:
        index[(t.employee.number,(t.source_data or {}).get('Código Formação'))].append(t)
    marks=defaultdict(lambda:defaultdict(list))
    linked_records=set()
    for r in DocumentaryRecord.objects.filter(kind='attendance_assertion'):
        p=r.payload;day=p.get('date');candidates=[]
        if not day or p.get('state') not in ('PRESENTE_DECLARADO','AUSENTE_DECLARADO'):continue
        for t in index[(p['number'],p.get('course_reference'))]:
            if not t.start.isoformat()<=day<=t.end.isoformat():continue
            cs=[context_by_id.get(c.get('event_id')) for c in p.get('claims',[])]
            if any(c and c.payload.get('Início documentado')==t.start.isoformat() and c.payload.get('Fim documentado')==t.end.isoformat() for c in cs):
                candidates.append(t)
        if len(candidates)==1:
            marks[candidates[0].pk][day].append((p['state'],r.pk))
            linked_records.add(r.pk)
    attendance_updated=0;attendance_verified=0
    for t in operational:
        days=marks.get(t.pk,{})
        if not days:continue
        proof=(t.source_data or {}).get('EPAL_DURATION_EVIDENCE',{})
        known_counts=set()
        if proof.get('explicit_session_count'):known_counts.add(proof['explicit_session_count'])
        for c in contexts:
            p=c.payload
            if t.employee_id in members[p.get('Contexto documental')] and p.get('Referência da ação na documentação')==(t.source_data or {}).get('Código Formação') and p.get('Início documentado')==t.start.isoformat() and p.get('Fim documentado')==t.end.isoformat():
                known_counts.update(p.get('Dias explicitamente declarados') or [])
        if known_counts!={t.days} or len(days)!=t.days or any(len({v[0] for v in rows})!=1 for rows in days.values()):continue
        presence=sum(rows[0][0]=='PRESENTE_DECLARADO' for rows in days.values())
        before={'attendance':t.attendance,'attendance_confirmed':t.attendance_confirmed}
        if t.attendance!=presence or not t.attendance_confirmed:
            t.attendance=presence;t.attendance_confirmed=True;attendance_updated+=1
        t.source_data={**t.source_data,'EPAL_ATTENDANCE_EVIDENCE':{'dates':{day:rows[0][0] for day,rows in days.items()},
            'record_ids':sorted({pk for rows in days.values() for _,pk in rows}),'review':KEY}}
        t.version+=1;t.save(update_fields=['attendance','attendance_confirmed','source_data','version'])
        attendance_verified+=1
        audit('HISTORICAL_ATTENDANCE_EVIDENCE',{'training_id':t.pk,'before':before,'after':{'attendance':presence,'attendance_confirmed':True},
            'basis':t.source_data['EPAL_ATTENDANCE_EVIDENCE'],'all_declared_sessions_covered':True})
    contexts_verified=0
    for c in contexts:
        p=c.payload;proofs=[]
        for t in operational:
            proof=(t.source_data or {}).get('EPAL_DURATION_EVIDENCE')
            if proof and t.employee_id in members[p.get('Contexto documental')] and p.get('Referência da ação na documentação')==(t.source_data or {}).get('Código Formação') and p.get('Início documentado')==t.start.isoformat() and p.get('Fim documentado')==t.end.isoformat():
                proofs.append(proof)
        totals={v['total_hours'] for v in proofs}
        if len(totals)!=1:continue
        values={'total_hours':next(iter(totals))}
        uniform={v['uniform_hours_per_day'] for v in proofs if v.get('uniform_hours_per_day')}
        counts={v['explicit_session_count'] for v in proofs if v.get('explicit_session_count')}
        if len(uniform)==1:values['hours_per_day']=next(iter(uniform))
        if len(counts)==1:values['days']=next(iter(counts))
        DocumentaryDecision.objects.create(record=c,actor='Conferência documental automática EPAL',reason='Carga horária explícita em certificados da mesma ação, período e participantes identificados.',
            evidence='; '.join(sorted({sha for v in proofs for sha in v['source_sha256']})),rule_signature=history.rule_signature(c),
            resolution={'state':'partial','confirmed_values':values,'scope':'Duração comprovada; não confirma notas ou presenças.'})
        contexts_verified+=1
        audit('CONTEXT_DURATION_EVIDENCE',{'context_record_id':c.pk,'values':values,'review':KEY})
    return {'daily_records_safely_linked':len(linked_records),'attendance_participations_verified':attendance_verified,
        'attendance_participations_changed':attendance_updated,'contexts_duration_verified':contexts_verified}


@transaction.atomic
def apply_review(folder):
    folder = Path(folder)
    existing = HistoricalReviewSnapshot.objects.filter(key=KEY).first()
    if existing:
        return existing.report
    def read(name):
        return json.loads((folder/(name+'.json')).read_text(encoding='utf-8'))
    proofs = read('CERTIFICADOS_ANALISADOS')
    initial_archives = read('REVISAO_DOS_171_PDFS')
    initial_missing = read('ENTREGAS_78_LISTA_INICIAL')
    grouped_tests = read('TESTES_DOCUMENTAIS_AGRUPADOS')
    from django.conf import settings
    root=Path(settings.PMF_CERTIFICATE_SOURCE_ROOT).resolve()
    verified_grade_sources=set()
    for test in grouped_tests:
        for source in test['sources']:
            if source['document'] in verified_grade_sources:continue
            path=(root/source['source']).resolve()
            if not path.is_relative_to(root) or hashlib.sha256(io_path(path).read_bytes()).hexdigest()!=source['document']:
                raise ValidationError('A fonte de avaliação mudou desde a conferência: '+source['source'])
            verified_grade_sources.add(source['document'])
    proof_index = {p['document']:p for p in proofs}
    from django.core.files.uploadedfile import SimpleUploadedFile
    from .documents import validate_pdf
    verified_bytes = {}
    # Validate every source before changing the DB. Never trust cached names alone.
    for p in proofs:
        data = Path(p['read_path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != p['document']:
            raise ValidationError('Fonte alterada: ' + p['document'])
        validate_pdf(SimpleUploadedFile('certificate.pdf', data, content_type='application/pdf'))
        verified_bytes[p['document']] = data
    numbers = {r['number'] for r in proofs}
    people = {e.number:e for e in Employee.objects.filter(number__in=numbers | {t['number'] for t in grouped_tests})}
    old_cert_records = list(DocumentaryRecord.objects.filter(kind='certificate_reference'))
    cert_by_sha = {r.payload.get('document'):r for r in old_cert_records}
    original_pending_ids = {r.pk for r in old_cert_records if r.state=='DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL' and not HistoricalCertificatePDF.objects.filter(source_record=r).exists()}
    changes = Counter()
    operational=list(Training.objects.exclude(source_key__in=EXCLUDED_KEYS).select_related('employee'))
    title_codes=defaultdict(set);code_titles={}
    for t in operational:
        code=(t.source_data or {}).get('Código Formação')
        if code:title_codes[normalize(t.title)].add(code);code_titles.setdefault(code,t.title)
    for p in proofs:
        if p['course'] and len(p['course_codes'])==1:title_codes[normalize(p['course'])].add(p['course_codes'][0])
    def action_key(p):
        codes=set(p['course_codes']) or title_codes.get(normalize(p['course']),set())
        return 'code:'+next(iter(codes)) if len(codes)==1 else 'title:'+normalize(p['course']) if p['course'] else None
    issue_periods=defaultdict(set)
    for p in proofs:
        if p['facts']['course_period']:
            issue_periods[(p['number'],action_key(p),p['facts'].get('issue_date'))].add(tuple(p['facts']['course_period']))
    for item in initial_archives:
        doc = HistoricalCertificatePDF.objects.select_for_update().get(pk=item['archive_id'])
        before = dict(doc.binding_evidence)
        if doc.training.source_key in EXCLUDED_KEYS:item['binding_review']='DUPLICADO_OPERACIONAL_EXCLUIDO'
        proof=proof_index.get(doc.sha256,{})
        if proof.get('quality_flags'):
            item['quality_flags']=proof['quality_flags']
            item['binding_review']='ACAO_OU_CARGA_CONJUNTA_POR_CONFIRMAR'
        doc.binding_evidence = {**before, 'review_state':item['binding_review'],
            'course_period_explicit':item['facts']['course_period'], 'layout':item['facts']['layout'], 'review':KEY}
        doc.save(update_fields=['binding_evidence'])
        if item['binding_review']!='PERIODO_CONCORDANTE':
            changes['previous_bindings_quarantined'] += 1
            audit('CERTIFICATE_BINDING_REVIEW', {'archive_id':doc.pk, 'before':before,'after':doc.binding_evidence,
                'delivery_preserved':True,'reason':'Data de emissão não comprova o período da formação.'})
    for p in proofs:
        if p['document'] not in cert_by_sha:
            payload = {'id':p['document'][:24], 'document':p['document'], 'number':p['number'],
                'name_literal':p.get('recipient_literal'), 'official_name':p['name'], 'course_literal':p['course'], 'sources':p['sources'],
                'identity_basis':p['identity_basis'],'verified_course_period':p['facts']['course_period'],
                'state':'CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA', 'delivered':None, 'review':KEY}
            r, _ = DocumentaryRecord.objects.get_or_create(source_key='epal-review-certificate:'+p['document'], defaults={
                'kind':'certificate_reference','employee':people[p['number']], 'state':payload['state'],
                'payload':payload,'payload_sha256':digest(payload),'package_sha256':digest({'review':KEY})})
            cert_by_sha[p['document']] = r
        if p['facts'].get('issue_date') and p['facts']['issue_date']>'2026-11-04':
            payload={'number':p['number'],'course_reference':p['course'] or ', '.join(p['course_codes']),
                'document':p['document'],'sources':p['sources'],'issue_date_literal':p['facts']['issue_date'],
                'course_period':p['facts']['course_period'],'reason':'Data de emissão impressa posterior à declaração histórica; obter PDF retificado sem presumir nova data.'}
            DocumentaryRecord.objects.get_or_create(source_key='epal-review-issue:'+p['document'],defaults={
                'kind':'document_issue','employee':people[p['number']],'state':'DATA_EMISSAO_POR_RETIFICAR',
                'payload':payload,'payload_sha256':digest(payload),'package_sha256':digest({'review':KEY})})
            changes['printed_issue_dates_held']+=1
            for doc in HistoricalCertificatePDF.objects.filter(sha256=p['document']):
                doc.binding_evidence={**doc.binding_evidence,'review_state':'DATA_EMISSAO_POR_RETIFICAR'}
                doc.save(update_fields=['binding_evidence'])
            continue
        if p.get('quality_flags'):
            changes['course_or_joint_duration_mappings_held']+=1
            continue
        if not p['candidate_training_ids']:
            period=p['facts']['course_period'];action=action_key(p)
            if not period or period[0]>period[1] or period[1]>'2026-11-04' or not action or not p['facts'].get('completion_literal'):
                continue
            if len(issue_periods[(p['number'],action,p['facts'].get('issue_date'))])!=1:
                changes['conflicting_certificate_periods_held']+=1;continue
            code=action[5:] if action.startswith('code:') else ''
            if any(t.employee.number==p['number'] and t.start.isoformat()<=period[1] and period[0]<=t.end.isoformat()
                and (not code or not (t.source_data or {}).get('Código Formação') or (t.source_data or {}).get('Código Formação')==code) for t in operational):
                changes['overlapping_operational_participations_held']+=1;continue
            related=[x for x in proofs if x['number']==p['number'] and action_key(x)==action and x['facts']['course_period']==period]
            totals={x['facts']['total_hours'] for x in related if x['facts']['total_hours'] is not None}
            uniform={x['facts']['uniform_hours_per_day'] for x in related if x['facts']['uniform_hours_per_day'] is not None}
            days={x['facts']['explicit_session_count'] for x in related if x['facts']['explicit_session_count'] is not None}
            part,created=HistoricalDocumentaryParticipation.objects.get_or_create(source_key='epal-docpart:'+digest([p['number'],action,period]),defaults={
                'employee':people[p['number']],'title':p['course'] or code_titles.get(code,code),'course_reference':code,
                'start':period[0],'end':period[1],'total_hours':Decimal(next(iter(totals))) if len(totals)==1 else None,
                'declared_days':next(iter(days)) if len(days)==1 else None,'hours_per_day':Decimal(next(iter(uniform))) if len(uniform)==1 else None,
                'completed':True,'learner_delivered':True,'delivery_basis':history.RULE,
                'evidence':{'source_sha256':[x['document'] for x in related],'identity_basis':p['identity_basis'],
                    'review':KEY,'course_period_explicit':period,'duration_conflict':len(totals)>1}})
            if created:
                changes['documentary_participations_created']+=1
                audit('CERTIFICATE_BACKED_PARTICIPATION',{'participation_id':part.pk,'employee_number':p['number'],'period':period,
                    'delivery_rule':history.RULE,'delivery_date':None,'evidence':part.evidence,
                    'missing_days_or_daily_hours_kept_null':True,'attendance_and_grade_not_inferred':True})
            record=cert_by_sha[p['document']]
            archive,created=HistoricalCertificatePDF.objects.get_or_create(documentary_participation=part,sha256=p['document'],defaults={
                'source_record':record,'content':verified_bytes[p['document']],'source_reference':p['sources'][0],
                'binding_evidence':{'employee_number':p['number'],'documentary_participation_id':part.pk,'review_state':'PERIODO_CONCORDANTE',
                    'course_period_explicit':period,'layout':p['facts']['layout'],'review':KEY,'identity_basis':p['identity_basis']}})
            if created:
                changes['pdfs_newly_archived']+=1
                audit('REVIEW_CERTIFICATE_ARCHIVED',{'archive_id':archive.pk,'documentary_participation_id':part.pk,'sha256':p['document'],'period':period})
            continue
        if len(p['candidate_training_ids']) != 1:
            continue
        t = Training.objects.select_related('employee').get(pk=p['candidate_training_ids'][0])
        if t.employee.number != p['number'] or [t.start.isoformat(),t.end.isoformat()] != p['facts']['course_period']:
            raise ValidationError('A participação mudou depois da análise.')
        state = TrainingDocumentState.objects.filter(training=t).first()
        if state and state.issued is False:
            changes['issuance_disagreements_held'] += 1
            continue
        record = cert_by_sha[p['document']]
        archive, created = HistoricalCertificatePDF.objects.get_or_create(training=t,sha256=p['document'],defaults={
            'source_record':record,'content':verified_bytes[p['document']], 'source_reference':p['sources'][0],
            'binding_evidence':{'employee_number':p['number'],'training_id':t.pk,
                'source_key':t.source_key,'review_state':'PERIODO_CONCORDANTE','course_period_explicit':p['facts']['course_period'],
                'layout':p['facts']['layout'],'review':KEY,'identity_basis':p['identity_basis']}})
        if created:
            changes['pdfs_newly_archived'] += 1
            audit('REVIEW_CERTIFICATE_ARCHIVED',{'archive_id':archive.pk,'training_id':t.pk,'sha256':p['document'],'proof':p['identity_basis'],'period':p['facts']['course_period']})
        state, _ = TrainingDocumentState.objects.get_or_create(training=t,defaults={'reference':'PDF SHA-256 '+p['document']})
        if state.issued is not True:
            state.issued=True;state.save(update_fields=['issued'])
        changes['new_delivery_confirmations'] += history.confirm_delivery(t,'Responsável pelo processo EPAL',
            'Certificado final existente no inventário histórico; período explícito; revisão '+KEY+'; SHA256 '+p['document'])

    # Duration is source evidence, not a guessed uniform distribution over days.
    for training_id in active_pdfs().filter(training__isnull=False).values_list('training_id',flat=True).distinct():
        t = Training.objects.select_for_update().get(pk=training_id)
        evidence = [proof_index[d.sha256] for d in active_pdfs().filter(training_id=training_id) if d.sha256 in proof_index]
        totals = {p['facts']['total_hours'] for p in evidence if p['facts']['total_hours'] is not None}
        uniforms = {p['facts']['uniform_hours_per_day'] for p in evidence if p['facts']['uniform_hours_per_day'] is not None}
        sessions = {p['facts']['explicit_session_count'] for p in evidence if p['facts']['explicit_session_count'] is not None}
        if len(totals)!=1:
            if totals:changes['duration_conflicts_held']+=1
            continue
        duration = {'total_hours':next(iter(totals)), 'source_sha256':[p['document'] for p in evidence], 'review':KEY,
            'uniform_hours_per_day':next(iter(uniforms)) if len(uniforms)==1 else None,
            'explicit_session_count':next(iter(sessions)) if len(sessions)==1 else None}
        old=dict(t.source_data)
        t.source_data={**old,'EPAL_DURATION_EVIDENCE':duration}
        if duration['uniform_hours_per_day'] and duration['explicit_session_count']==t.days:
            t.hours_per_day=Decimal(duration['uniform_hours_per_day'])
            changes['uniform_daily_hours_verified']+=1
        t.version+=1;t.save(update_fields=['source_data','hours_per_day','version'])
        changes['total_duration_verified']+=1
        audit('HISTORICAL_DURATION_EVIDENCE',{'training_id':t.pk,'duration':duration,'previous_duration_evidence':old.get('EPAL_DURATION_EVIDENCE'), 'attendance_not_inferred':True})

    changes.update(apply_attendance_and_context_hours())
    canonical_results=[]
    existing_contexts=list(DocumentaryRecord.objects.filter(kind='context'))
    for test in grouped_tests:
        if test['state']!='CONCORDANTE':
            continue
        context_key='epal-review-context:'+':'.join([test['course_code'],test['group'],test['start'],test['end']])
        context_payload={'Contexto documental':context_key,'Referência da ação na documentação':test['course_code'],
            'Título literal':test['course_code']+' — avaliações documentais', 'Grupo de origem':test['group'],
            'Início documentado':test['start'],'Fim documentado':test['end'], 'Dias explicitamente declarados':[],
            'Horas/dia explicitamente declaradas':[], 'scope':'Período declarado na folha ou associado a coorte única por ação, grupo e matrícula; não cria turma operacional.'}
        choices=[c for c in existing_contexts if c.payload.get('Referência da ação na documentação')==test['course_code']
            and str(c.payload.get('Grupo de origem'))==test['group'] and c.payload.get('Início documentado')==test['start'] and c.payload.get('Fim documentado')==test['end']]
        if choices:
            context=min(choices,key=lambda c:c.pk);context_key=context.payload['Contexto documental']
        else:
            context,created=DocumentaryRecord.objects.get_or_create(source_key=context_key,defaults={'kind':'context','state':'CONTEXTO_DOCUMENTAL',
                'payload':context_payload,'payload_sha256':digest(context_payload),'package_sha256':digest({'review':KEY})})
            if created:existing_contexts.append(context)
        nominal_key='epal-review-nominal:'+digest([test['number'],context_key])
        nominal_payload={'number':test['number'],'event_id':context_key,'sources':[{'source':s['source'],'document':s['document'],
            'sheet':s['sheet'],'row':s['row']} for s in test['sources']], 'scope':'Registo nominal documental; sem conclusão ou duração presumidas.'}
        nominal=DocumentaryRecord.objects.filter(kind='nominal',employee=people[test['number']],payload__event_id=context_key).first()
        if not nominal:
            nominal,_=DocumentaryRecord.objects.get_or_create(source_key=nominal_key,defaults={'kind':'nominal','employee':people[test['number']],
                'state':'DOCUMENTADO','payload':nominal_payload,'payload_sha256':digest(nominal_payload),'package_sha256':digest({'review':KEY})})
        nominal_key=nominal.source_key
        participation_key='epal-docpart:'+digest([test['number'],'code:'+test['course_code'],[test['start'],test['end']]])
        matches=[t for t in operational if t.employee.number==test['number'] and (t.source_data or {}).get('Código Formação')==test['course_code']
            and t.start.isoformat()==test['start'] and t.end.isoformat()==test['end']]
        part=None;created=False
        if not matches:
            part,created=HistoricalDocumentaryParticipation.objects.get_or_create(source_key=participation_key,defaults={
            'employee':people[test['number']],'title':context.payload.get('Título literal') or test['course_code'],'course_reference':test['course_code'],
            'start':test['start'],'end':test['end'],'completed':None,
            'evidence':{'context_id':context_key,'source_sha256':sorted({s['document'] for s in test['sources']}),
                'review':KEY,'scope':'Participação com avaliação documental; conclusão, presenças, duração e certificado não presumidos.'}})
        if created:
            changes['assessment_backed_participations_created']+=1
            audit('ASSESSMENT_BACKED_PARTICIPATION',{'participation_id':part.pk,'employee_number':test['number'],
                'context':context_key,'completion':None,'delivery':None,'hours':None})
        fact_key='epal-review-test:'+digest([test[k] for k in ['number','course_code','group','start','end','exam_date','stage']])
        payload={**test,'course_reference':test['course_code'],'group_reference':test['group'],'date':test['exam_date'],
            'value':test['values'][0]['value'],'scale':test['values'][0]['scale'],'event_id':context_key,
            'documentary_participation_key':nominal_key,'scope':'Valor original na escala da fonte. Não é nota académica /20 ou aprovação.'}
        payload['historical_participation_id']=part.pk if part else None
        payload['training_id']=matches[0].pk if len(matches)==1 else None
        if test.get('existing_fact_id'):
            fact=DocumentaryRecord.objects.get(pk=test['existing_fact_id']);created=False;changes['existing_tests_corroborated']+=1
        else:
            fact,created=DocumentaryRecord.objects.get_or_create(source_key=fact_key,defaults={'kind':'original_test_reviewed','employee':people[test['number']],
                'state':'VALOR_LITERAL_CONCORDANTE','payload':payload,'payload_sha256':digest(payload),'package_sha256':digest({'review':KEY})})
        changes['canonical_tests_created']+=int(created)
        for source_id in test['record_ids']:
            r=DocumentaryRecord.objects.get(pk=source_id)
            prior=history.latest_decision(r)
            if prior and prior.resolution.get('state')=='resolved':continue
            resolution={'state':'resolved','documentary_context_id':context_key,'documentary_participation_key':nominal_key,
                'canonical_fact_id':fact.pk,'selected_source_claim':{'value':payload['value'],'scale':payload['scale'],'date':test['exam_date']},
                'scope':payload['scope'],'automatic_rule':'IDENTIDADE_VALIDADA_CODIGO_GRUPO_COORTE_E_VALORES_CONCORDANTES'}
            d=DocumentaryDecision.objects.create(record=r,actor='Conferência documental automática EPAL',reason='Cópias/versões concordantes do mesmo teste, identificadas por formando, grupo, fase e data.',
                evidence='; '.join(s['document']+':'+s['sheet']+':'+str(s['row']) for s in test['sources'])[:8000],
                rule_signature=history.rule_signature(r),resolution=resolution)
            audit('AUTOMATIC_TEST_ASSOCIATION',{'record_id':r.pk,'decision_id':d.pk,'canonical_fact_id':fact.pk,'resolution':resolution})
            changes['test_source_occurrences_resolved']+=1
        canonical_results.append({'record_id':fact.pk,'number':test['number'],'context':context_key,'participation':nominal_key,
            'value':payload['value'],'scale':payload['scale'],'stage':test['stage'],'date':test['exam_date'],'source_occurrences':len(test['record_ids'])})

    for row in initial_missing:
        docs=list(active_pdfs().filter(training_id=row['training_id']).values('pk','sha256'))
        row['pdfs']=docs;row['resolved']=bool(docs)
        numbers={proof_index[d['sha256']]['facts'].get('certificate_number') for d in docs if d['sha256'] in proof_index}
        numbers.discard(None)
        if not row['certificate_number'] and len(numbers)==1:
            row['certificate_number']=next(iter(numbers))
    original_associations=[]
    for pk in sorted(original_pending_ids):
        r=DocumentaryRecord.objects.select_related('employee').get(pk=pk)
        docs=list(active_pdfs().filter(source_record=r).values('pk','training_id','documentary_participation_id','sha256'))
        original_associations.append({'record_id':pk,'number':r.employee.number,'name':r.employee.name,
            'course':r.payload.get('course_literal'),'document':r.payload.get('document'),'resolved':bool(docs),'pdfs':docs})
    image_sources=read('CERTIFICADOS_IMAGEM_PENDENTES')
    for image in image_sources:
        data=Path(image['read_path']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=image['document']:raise ValidationError('Certificado digitalizado alterado.')
        record,created=DocumentaryRecord.objects.get_or_create(source_key='review-image-'+image['document'],defaults={
            'kind':'document_issue','state':'IDENTIDADE_OU_PERIODO_DIGITALIZADO_POR_COMPROVAR','payload':image,
            'payload_sha256':digest(image),'package_sha256':digest([KEY,image['document']])})
        if created:
            changes['image_certificates_identity_or_period_held']+=1
            audit('CERTIFICATE_IMAGE_REVIEW',{'record_id':record.pk,'document':image['document'],'delivery_not_inferred':True})
    report={'key':KEY,'analysis_summary':read('RESUMO_ANALISE'),'changes':dict(changes),'archive_initial':initial_archives,
        'initial_78':initial_missing,'initial_134':original_associations,'canonical_tests':canonical_results,
        'context_sources':read('CONTEXTOS_FONTES'),
        'certificate_sources':[{k:v for k,v in p.items() if k!='pages_text'} for p in proofs],
        'image_certificate_sources':image_sources,
        'delivery_rule_preserved':history.RULE, 'note':'Não se escolheu uma versão final por nome da pasta ou por maioria; períodos discordantes ficaram em revisão.'}
    HistoricalReviewSnapshot.objects.create(key=KEY,actor='Conferência documental automática EPAL',report=report)
    audit('HISTORICAL_REVIEW_APPLIED',{'key':KEY,'changes':dict(changes),'original_78_resolved':sum(r['resolved'] for r in initial_missing),
        'original_134_resolved':sum(r['resolved'] for r in original_associations)})
    return report


def add_documentary_statistics(stats, parts):
    parts=list(parts)
    if not parts:return stats
    result=dict(stats);n=len(parts)
    result['total']+=n
    result['planned_hours']+=sum((p.total_hours for p in parts if p.total_hours is not None),Decimal(0))
    delivered=sum(p.learner_delivered is True for p in parts)
    known=sum(p.learner_delivered is not None for p in parts)
    completed=sum(p.completed is True for p in parts)
    for key,value in [('certificates',delivered),('learner_delivery_known',known),('completed_count',completed),
            ('completion_known',sum(p.completed is not None for p in parts)),('completion_pending',sum(p.completed is None for p in parts)),
            ('certificates_pending',n-known),('attendance_unknown',n),('hours_unknown',n),
            ('issued_count',delivered),('issued_known',known),('issued_pending',n-known),('hr_delivery_pending',sum(p.hr_delivered is None for p in parts))]:
        result[key]=result.get(key,0)+value
    if delivered or result.get('learner_delivery_value') is not None:
        result['learner_delivery_value']=(result.get('learner_delivery_value') or 0)+delivered
    if delivered or result.get('issued_value') is not None:
        result['issued_value']=(result.get('issued_value') or 0)+delivered
    result['attendance_provisional']=True
    return result


def grouped_pending():
    records=list(DocumentaryRecord.objects.filter(kind__in=['context','held_assertion','unlinked_test','certificate_reference','document_issue']).select_related('employee').prefetch_related('decisions'))
    archived=set(active_pdfs().values_list('source_record_id',flat=True))
    members=defaultdict(set)
    for r in DocumentaryRecord.objects.filter(kind='nominal').values('employee__number','payload'):
        members[r['payload'].get('event_id')].add(r['employee__number'])
    snap=latest_snapshot()
    context_sources=snap.report.get('context_sources',{}) if snap else {}
    groups={};affected=set();unknown_participation_records=set();doc_participations=set();known_trainings=set();known_documentary=set()
    training_index=defaultdict(list)
    for t in Training.objects.exclude(source_key__in=EXCLUDED_KEYS).select_related('employee'):
        code=t.source_course_code or (t.source_data or {}).get('Código Formação')
        for course_key in {normalize(v) for v in [code,t.title] if v}:
            training_index[(t.employee.number,course_key,t.start.isoformat(),t.end.isoformat())].append(t.pk)
    documentary_index=defaultdict(list)
    for part in HistoricalDocumentaryParticipation.objects.select_related('employee'):
        for course_key in {normalize(v) for v in [part.course_reference,part.title] if v}:
            documentary_index[(part.employee.number,course_key,part.start.isoformat(),part.end.isoformat())].append(part.pk)
    documentary_projection=defaultdict(set)
    for training_id,doc_ids in HistoricalInterfaceLink.objects.filter(participation__active=True).values_list('training_id','participation__documentary_participation_ids'):
        for pk in doc_ids:documentary_projection[pk].add(training_id)
    for r in records:
        last=history.latest_decision(r)
        if last and last.resolution.get('state')=='resolved':continue
        p=r.payload
        if r.kind=='context' and not history.missing_context_fields(r):continue
        if r.kind=='certificate_reference' and (r.state not in ['DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL','CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA'] or r.pk in archived):continue
        course=p.get('course_reference') or p.get('course_literal') or p.get('Referência da ação na documentação') or p.get('Título literal') or 'Formação por associar'
        if r.kind=='unlinked_test':
            course='SHST' if 'shst' in normalize(p.get('source')).split() else 'COAS' if 'coas' in normalize(p.get('source')).split() else course
        problem='Documento por retificar' if r.kind=='document_issue' else 'Conflito real' if history.has_conflicting_claims(r) else 'Associação em falta' if r.kind in ('unlinked_test','certificate_reference') else 'Informação em falta ou sem prova'
        claims=p.get('claims') or []
        source_items=[c.get('source',{}) for c in claims]
        if not source_items:
            source_items=[{'document':p.get('document'),'source':p.get('source') or '; '.join(p.get('sources',[]))}] if r.kind!='context' else context_sources.get(p.get('Contexto documental')) or [{'document':None,'source':'Contexto documental '+str(p.get('Contexto documental'))}]
        ns={r.employee.number} if r.employee else members.get(p.get('Contexto documental'),set())
        affected.update(ns)
        events={c.get('event_id') for c in claims if c.get('event_id')}
        if r.kind=='context':events.add(p.get('Contexto documental'))
        for e in events:
            for n in ns:doc_participations.add((n,e))
        linked=set()
        doc_linked=set()
        for n in ns:
            matches=training_index.get((n,normalize(course),p.get('start_declared') or p.get('Início documentado'),p.get('end_declared') or p.get('Fim documentado')),[])
            if len(matches)==1:linked.update(matches)
            docs=documentary_index.get((n,normalize(course),p.get('start_declared') or p.get('Início documentado'),p.get('end_declared') or p.get('Fim documentado')),[])
            if len(docs)==1:
                projections=documentary_projection.get(docs[0],set())
                if len(projections)==1:linked.update(projections)
                elif not projections:doc_linked.update(docs)
        known_trainings.update(linked)
        known_documentary.update(doc_linked)
        if r.employee_id and not linked and not doc_linked:unknown_participation_records.add(r.pk)
        for s in source_items:
            sha=s.get('document') or s.get('source') or 'Sem fonte identificada'
            key=(course,problem,sha)
            g=groups.setdefault(key,{'course':course,'problem':problem,'source_document':s.get('document'),
                '_records':set(),'_numbers':set(),'_sources':set(),'_trainings':set(),'_documentary':set()})
            g['_records'].add(r.pk);g['_numbers'].update(ns);g['_trainings'].update(linked)
            g['_documentary'].update(doc_linked)
            if s.get('source'):g['_sources'].add(s['source'])
    result=[]
    for g in groups.values():
        result.append({'course':g['course'],'problem':g['problem'],'source_document':g['source_document'],
            'occurrences':len(g['_records']),'distinct_learners':len(g['_numbers']),'known_operational_participations':len(g['_trainings']),
            'known_documentary_participations':len(g['_documentary']),
            'distinct_identified_participations':len(g['_trainings'])+len(g['_documentary']),
            'record_ids':sorted(g['_records']),'numbers':sorted(g['_numbers']),'sources':sorted(g['_sources']),
            'same_binary_source_multiple_locations':len(g['_sources'])>1 and bool(g['source_document'])})
    return {'groups':sorted(result,key=lambda g:(g['course'],g['problem'],str(g['source_document']))),
        'distinct_learners':len(affected),'known_operational_participations':len(known_trainings),
        'known_documentary_participations':len(known_documentary),'distinct_identified_participations':len(known_trainings)+len(known_documentary),
        'documentary_participation_links':len(doc_participations),'individual_records_without_unique_operational_participation':len(unknown_participation_records),
        'note':'As ligações documentais podem representar versões do mesmo evento; não são somadas como participações operacionais únicas.'}


@transaction.atomic
def recover_concordant_attendance():
    """A template copy must not invalidate concordant eligible attendance sources."""
    key=KEY+'_ATTENDANCE'
    previous=HistoricalReviewSnapshot.objects.filter(key=key).first()
    if previous:return previous.report
    from django.conf import settings
    from datetime import date
    root=Path(settings.PMF_CERTIFICATE_SOURCE_ROOT).resolve();verified=set();prepared=[]
    for record in DocumentaryRecord.objects.filter(kind='held_assertion',state__in=['PRESENTE_DECLARADO','AUSENTE_DECLARADO']).select_related('employee').prefetch_related('decisions'):
        p=record.payload;last=history.latest_decision(record)
        if last and last.resolution.get('state')=='resolved':continue
        if not p.get('documentary_consistent') or not record.employee or p.get('number')!=record.employee.number:continue
        try:day=date.fromisoformat(p.get('date',''))
        except (ValueError,TypeError):continue
        if day>history.CUTOFF:continue
        claims=[c for c in p.get('claims',[]) if c.get('eligible_documentary_assertion') and not c.get('source',{}).get('financial_register') and not c.get('source',{}).get('preparatory_or_derived_or_synthetic_source')]
        if not claims or {c.get('status') for c in claims}!={record.state} or any(c.get('number')!=record.employee.number or c.get('date')!=p['date'] for c in claims):continue
        for claim in claims:
            source=claim['source'];sha=source.get('document')
            if not sha or not source.get('source'):raise ValidationError('Fonte de assiduidade sem referência verificável.')
            if sha not in verified:
                path=(root/source['source']).resolve()
                if not path.is_relative_to(root) or hashlib.sha256(io_path(path).read_bytes()).hexdigest()!=sha:raise ValidationError('Fonte de assiduidade alterada.')
                verified.add(sha)
        prepared.append((record,claims))
    for record,claims in prepared:
        p={**record.payload,'claims':claims,'corroboration':{'rule':key,'raw_record_id':record.pk,
            'scope':'Marca diária concordante em fonte elegível; não presume assinatura, presença em sessões sem marca, carga horária ou conclusão.'}}
        canonical,created=DocumentaryRecord.objects.get_or_create(source_key='epal-documentary:attendance_assertion:'+p['id'],defaults={
            'kind':'attendance_assertion','employee':record.employee,'state':record.state,'payload':p,
            'payload_sha256':digest(p),'package_sha256':digest([key,record.package_sha256])})
        if canonical.employee_id!=record.employee_id or canonical.state!=record.state or canonical.payload.get('date')!=p['date']:raise ValidationError('Marca canónica divergente.')
        resolution={'state':'resolved','canonical_fact_id':canonical.pk,'selected_source_claim':claims[0],
            'excluded_template_claims':len(record.payload['claims'])-len(claims),'course_attendance_not_inferred':True}
        DocumentaryDecision.objects.create(record=record,actor='Conferência documental automática EPAL',
            reason='Fontes elegíveis concordantes; cópias preparatórias não anulam a marca comprovada.',
            evidence=json.dumps([c['source'] for c in claims],ensure_ascii=False),rule_signature=history.rule_signature(record),resolution=resolution)
        audit('ATTENDANCE_SOURCES_RECONCILED',{'record_id':record.pk,'canonical_fact_id':canonical.pk,'resolution':resolution})
    snapshot=latest_snapshot()
    report=json.loads(json.dumps(snapshot.report,ensure_ascii=False))
    report['changes']['attendance_assertions_recovered']=len(prepared)
    report['attendance_recovery_rule']=key
    HistoricalReviewSnapshot.objects.create(key=key,actor='Conferência documental automática EPAL',report=report)
    return report

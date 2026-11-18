"""Evidence-preserving historical delivery, archive and exception services."""
import hashlib
import io
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from .models import (AuditEvent, DocumentaryRecord, DocumentaryDecision, Employee,
                     Training, TrainingDocumentState, HistoricalCertificatePDF, HistoricalDeliveryDirective)
from .documentary_import import digest

RULE = 'EPAL_RESPONSAVEL_ENTREGA_HISTORICA_20261104'
CUTOFF = date(2026, 11, 4)
DECLARATION = ('O responsável pelo processo confirmou em 04/11/2026 que todos os certificados já emitidos '
               'relativos às formações concluídas foram entregues aos respetivos formandos no final da formação. '
               'Não confirma emissão de modelos, formações futuras, notas, horas ou presenças; não presume data exata de entrega.')


def norm(value):
    value = unicodedata.normalize('NFKD', value or '').casefold()
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).strip()


def field_label(record):
    if record.kind=='document_issue':return 'Identidade / período do certificado digitalizado' if record.state=='IDENTIDADE_OU_PERIODO_DIGITALIZADO_POR_COMPROVAR' else 'Data de emissão no PDF'
    if record.kind == 'context':
        return 'Horas, duração e período do contexto'
    if record.kind in ('unlinked_test',):
        return 'Associação do teste à participação'
    if record.kind == 'certificate_reference':
        return 'Associação / arquivo do certificado'
    if record.payload.get('tipo') == 'presença':
        return 'Presença diária'
    return 'Resultado / escala do teste'


def rule_signature(record):
    r = record.payload
    candidates = [{'value': c.get('value'), 'scale': c.get('scale'), 'status': c.get('status')}
                  for c in r.get('claims', [])]
    return digest([record.kind, field_label(record), r.get('course_reference') or r.get('Referência da ação na documentação'),
                   r.get('state'), candidates, missing_context_fields(record) if record.kind == 'context' else None])


def has_conflicting_claims(record):
    values=set()
    for c in record.payload.get('claims',[]):
        source=c.get('source',{})
        if not c.get('eligible_documentary_assertion') or source.get('financial_register') or source.get('preparatory_or_derived_or_synthetic_source'):continue
        if c.get('status') in ('PRESENTE_DECLARADO','AUSENTE_DECLARADO'):values.add(('status',c['status']))
        elif c.get('value') is not None:values.add(('value',Decimal(str(c['value'])),str(c.get('scale'))))
    return len(values)>1 or record.state=='CONFLITO'


def missing_context_fields(record):
    mapping = {'hours_per_day': 'Horas/dia explicitamente declaradas', 'days': 'Dias explicitamente declarados',
               'start': 'Início documentado', 'end': 'Fim documentado', 'group': 'Grupo de origem'}
    confirmed = set()
    for decision in record.decisions.all():
        confirmed.update(decision.resolution.get('confirmed_values', {}))
    if 'total_hours' in confirmed:
        confirmed.add('hours_per_day')
    missing = [key for key, source_key in mapping.items() if not record.payload.get(source_key) and key not in confirmed]
    latest_hours = next((d for d in record.decisions.all() if 'hours_per_day' in d.resolution.get('confirmed_values', {})), None)
    if latest_hours and latest_hours.resolution.get('operational_binding_pending'):
        missing.append('operational_binding')
    return missing


def apply_documented_hours(record, value, actor):
    p = record.payload
    start, end = p.get('Início documentado'), p.get('Fim documentado')
    durations = p.get('Dias explicitamente declarados') or []
    people = list(DocumentaryRecord.objects.filter(kind='nominal', payload__event_id=p.get('Contexto documental')).values_list('employee_id', flat=True).distinct())
    linked = []
    if not start or not end or len(set(durations)) != 1:
        return linked, True
    for employee_id in people:
        candidates = []
        for t in Training.objects.select_for_update().filter(employee_id=employee_id, start=start, end=end):
            code = (t.source_data or {}).get('Código Formação')
            course_match = (code and code == p.get('Referência da ação na documentação')) or norm(t.title) == norm(p.get('Título literal'))
            if course_match and t.days == durations[0]:
                candidates.append(t)
        if len(candidates) != 1:
            continue
        t = candidates[0]
        before = str(t.hours_per_day)
        t.hours_per_day = value
        try:
            t.full_clean()
        except ValidationError:
            continue
        if before == 'None' or Decimal(before) != value:
            t.version += 1
            t.save(update_fields=['hours_per_day', 'version'])
            AuditEvent.objects.create(actor=actor, action='DOCUMENTED_HOURS_APPLIED', detail=json.dumps({
                'context_record_id': record.pk, 'training_id': t.pk, 'before': before, 'after': str(value),
                'basis': 'Mesma identidade, ação, período e duração documental; decisão fundamentada de horas/dia.'}, ensure_ascii=False))
        linked.append(t.pk)
    return linked, len(linked) != len(people) or not people


def latest_decision(record):
    return record.decisions.first()


@transaction.atomic
def decide(user, ids, choice, reason, evidence, expected, hours=None):
    if not reason.strip() or not evidence.strip() or max(len(reason), len(evidence)) > 2000:
        raise ValidationError('Indique a justificação e a referência da evidência.')
    rows = list(DocumentaryRecord.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if not rows or len(rows) != len(set(ids)) or len(rows) > 100:
        raise ValidationError('Selecione entre 1 e 100 registos válidos.')
    signatures = {rule_signature(r) for r in rows}
    if len(signatures) != 1 or signatures != {expected}:
        raise ValidationError('O lote deve partilhar exatamente a mesma regra e os mesmos valores de origem.')
    for r in rows:
        if r.kind not in ('held_assertion','unlinked_test','certificate_reference','context'):
            raise ValidationError('Este registo já está documentado e não exige nova confirmação.')
        if latest_decision(r) and latest_decision(r).resolution.get('state') == 'resolved':
            raise ValidationError('O lote contém registos já resolvidos; atualize a lista.')
        if choice == 'retain':
            resolution = {'state': 'pending', 'reason': 'Evidência ainda insuficiente'}
        elif choice == 'hours':
            if r.kind != 'context':
                raise ValidationError('Esta decisão aplica-se apenas às horas do contexto de formação.')
            try:
                value = Decimal(str(hours))
                if not value.is_finite() or not Decimal('0') < value <= Decimal('24'):
                    raise ValueError
            except (InvalidOperation, ValueError, TypeError):
                raise ValidationError('Indique horas/dia comprovadas, entre 0 e 24.')
            resolution = {'state': 'partial', 'confirmed_values': {'hours_per_day': str(value)},
                          'scope': 'Horas/dia confirmadas por decisão documentada; não altera presenças, calendário ou conclusão.'}
            linked, pending = apply_documented_hours(r, value, user.get_username())
            resolution.update(operational_training_ids=linked, operational_binding_pending=pending)
        elif choice.startswith('training:'):
            if len(rows) != 1 or r.kind not in ('unlinked_test', 'certificate_reference'):
                raise ValidationError('Uma associação à participação é feita por registo.')
            try:
                training = Training.objects.get(pk=int(choice.split(':')[1]), employee_id=r.employee_id)
            except (ValueError, Training.DoesNotExist):
                raise ValidationError('A participação não pertence a este trabalhador.')
            if r.kind == 'certificate_reference' and (r.state not in ('DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL','CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA') or training.status != 'Concluído' or training.end > CUTOFF):
                raise ValidationError('A regra histórica exige certificado final e formação concluída até ao limite histórico.')
            if r.kind=='certificate_reference':
                from .historical_review import latest_snapshot
                snapshot=latest_snapshot()
                proof=next((p for p in snapshot.report['certificate_sources'] if p['document']==r.payload.get('document')),None) if snapshot else None
                if proof and (proof.get('quality_flags') or (proof['facts'].get('issue_date') or '')>'2026-11-04'):
                    raise ValidationError('O documento exige retificação ou mapeamento da ação/carga conjunta. A entrega já confirmada mantém-se.')
                if proof and [training.start.isoformat(),training.end.isoformat()]!=proof['facts']['course_period']:
                    raise ValidationError('O período da participação não corresponde ao período explícito no certificado.')
            if r.kind == 'certificate_reference' and not certificate_action_matches(r, training):
                raise ValidationError('A ação de formação não corresponde ao certificado de origem.')
            resolution = {'state': 'association_confirmed' if r.kind == 'certificate_reference' else 'resolved', 'training_id': training.pk, 'source_key': training.source_key,
                          'scope': 'Associação documentada pelo responsável; não confirma nota final, presença ou emissão.'}
        else:
            if r.kind != 'held_assertion':
                raise ValidationError('Esta exceção requer associação documental, não escolha de um valor.')
            if not has_conflicting_claims(r):
                raise ValidationError('Escolher um valor não resolve a falta de escala, data ou fonte.')
            try:
                candidate = r.payload['claims'][int(choice)]
            except (KeyError, ValueError, IndexError, TypeError):
                raise ValidationError('Escolha um valor efetivamente existente na fonte.')
            if int(choice) < 0 or (candidate.get('value') is None and candidate.get('status') not in ('PRESENTE_DECLARADO', 'AUSENTE_DECLARADO')):
                raise ValidationError('A fonte não contém um valor resolúvel.')
            source = candidate.get('source', {})
            if source.get('financial_register') or source.get('preparatory_or_derived_or_synthetic_source'):
                raise ValidationError('Registos financeiros ou derivados não comprovam resultados individuais.')
            resolution = {'state': 'resolved', 'selected_source_claim': candidate,
                          'scope': 'Decisão documental; não cria nota final, aprovação ou conclusão.'}
        previous = latest_decision(r)
        if previous and previous.resolution == resolution and previous.reason == reason and previous.evidence == evidence:
            continue
        decision = DocumentaryDecision.objects.create(record=r, actor=user.get_username(), reason=reason,
            evidence=evidence, rule_signature=expected, resolution=resolution)
        AuditEvent.objects.create(actor=user.get_username(), action='DOCUMENTARY_EXCEPTION_DECISION',
            detail=json.dumps({'record_id': r.pk, 'decision_id': decision.pk, 'previous_decision_id': previous.pk if previous else None,
                               'resolution': resolution, 'reason': reason, 'evidence': evidence}, ensure_ascii=False))
        if r.kind == 'certificate_reference' and resolution.get('training_id'):
            from django.conf import settings
            root = getattr(settings, 'PMF_CERTIFICATE_SOURCE_ROOT', '')
            if not root:
                raise ValidationError('A pasta de certificados não está configurada; a decisão não foi gravada.')
            directive = HistoricalDeliveryDirective.objects.get(key=RULE)
            official_numbers = list(DocumentaryRecord.objects.filter(kind='nominal').values_list('employee__number', flat=True).distinct())
            regularize(root, official_numbers, actor=user.get_username(), only_records=[r.pk])
    return len(rows)


def certificate_action_matches(record, training):
    d = training.source_data or {}
    code = training.source_course_code or d.get('Código Formação') or d.get('Código de Formação')
    paths = ' '.join(record.payload.get('sources', []))
    code_match = bool(code and re.search(r'(?<![A-Za-z0-9])' + re.escape(str(code)) + r'(?![A-Za-z0-9])', paths, re.I))
    return code_match or norm(training.title) == norm(record.payload.get('course_literal'))


def certificate_candidates(record, root=None):
    r = record.payload
    if r.get('state') not in ('DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL','CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA') or not record.employee_id:
        return []
    from .historical_review import latest_snapshot
    from .certificate_evidence import certificate_facts
    snapshot=latest_snapshot()
    proof=next((p for p in snapshot.report['certificate_sources'] if p['document']==r.get('document')),None) if snapshot else None
    if proof and (proof.get('quality_flags') or (proof['facts'].get('issue_date') or '')>'2026-11-04'):return []
    period=proof['facts']['course_period'] if proof else None
    if not period and root:
        from pypdf import PdfReader
        import io
        for source in r.get('sources',[]):
            if ' :: ' in source or not source.lower().endswith('.pdf'):continue
            path=(Path(root)/source).resolve()
            if not path.is_relative_to(Path(root).resolve()):continue
            try:
                data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()!=r.get('document'):continue
                pdf=PdfReader(io.BytesIO(data))
                period=certificate_facts([page.extract_text() or '' for page in pdf.pages])['course_period']
            except (OSError,ValueError):continue
            break
    if not period:return []
    decision = latest_decision(record)
    result = []
    for t in Training.objects.filter(employee_id=record.employee_id, status='Concluído', end__lte=CUTOFF):
        if decision and decision.resolution.get('training_id') and t.pk!=decision.resolution['training_id']:continue
        if certificate_action_matches(record,t) and [t.start.isoformat(),t.end.isoformat()]==period:
            result.append(t)
    return result


def confirm_delivery(training, actor, evidence):
    if training.status != 'Concluído' or training.end > CUTOFF:
        raise ValidationError('Regra histórica não aplicável a formação não concluída ou posterior ao limite.')
    state, _ = TrainingDocumentState.objects.get_or_create(training=training, defaults={'reference': evidence[:500]})
    if state.issued is not True:
        raise ValidationError('A entrega histórica exige emissão previamente comprovada.')
    if state.historical_delivery_basis == RULE and state.learner_delivered is True:
        return False
    before = {'learner_delivered': state.learner_delivered, 'learner_date': str(state.learner_date) if state.learner_date else None}
    state.learner_delivered = True
    state.learner_responsible = 'Responsável pelo processo EPAL'
    state.historical_delivery_basis = RULE
    state.revision += 1
    state.full_clean()
    state.save()
    AuditEvent.objects.create(actor=actor, action='HISTORICAL_CERTIFICATE_DELIVERY', detail=json.dumps({
        'training_id': training.pk, 'employee_number': training.employee.number, 'rule': RULE,
        'declaration': DECLARATION, 'evidence': evidence, 'before': before,
        'after': {'learner_delivered': True, 'learner_date': state.learner_date.isoformat() if state.learner_date else None},
        'hr_delivery_changed': False}, ensure_ascii=False))
    return True


@transaction.atomic
def regularize(root, official_numbers, actor='Responsável pelo processo EPAL', only_records=None):
    """Only real, single-recipient PDFs with a unique employee/course/date binding."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from .documents import validate_pdf
    counts = {'delivery_confirmations_created': 0, 'pdfs_associated_created': 0, 'pdfs_existing': 0,
              'ambiguous_certificate_bindings': 0, 'pdf_reading_issues': 0}
    issues = []
    initial_issued_ids = list(TrainingDocumentState.objects.filter(issued=True, training__status='Concluído',
        training__end__lte=CUTOFF, training__employee__number__in=official_numbers).values_list('training_id', flat=True))
    initial_certs = list(DocumentaryRecord.objects.filter(kind='certificate_reference',
        state='DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL').values_list('pk', flat=True))
    directive, created = HistoricalDeliveryDirective.objects.get_or_create(key=RULE, defaults={
        'declaration': DECLARATION, 'actor': actor,
        'scope': {'already_issued_training_ids': initial_issued_ids, 'certificate_record_ids': initial_certs,
                  'cutoff': CUTOFF.isoformat()}})
    if created:
        AuditEvent.objects.create(actor=actor, action='HISTORICAL_DELIVERY_DIRECTIVE', detail=json.dumps({
            'rule': RULE, 'declaration': DECLARATION, 'scope': directive.scope}, ensure_ascii=False))
    for s in TrainingDocumentState.objects.select_related('training__employee').filter(
            issued=True, training__status='Concluído', training__end__lte=CUTOFF,
            training__employee__number__in=official_numbers, training_id__in=directive.scope['already_issued_training_ids']):
        counts['delivery_confirmations_created'] += confirm_delivery(s.training, actor, 'Emissão já confirmada no registo #' + str(s.pk))
    from .historical_review import latest_snapshot
    snapshot=latest_snapshot()
    eligible_ids=set(directive.scope['certificate_record_ids'])
    if snapshot:
        hashes=[p['document'] for p in snapshot.report['certificate_sources']]
        eligible_ids.update(DocumentaryRecord.objects.filter(kind='certificate_reference',payload__document__in=hashes).values_list('pk',flat=True))
    certs = list(DocumentaryRecord.objects.filter(pk__in=eligible_ids).select_related('employee'))
    recipients = {}
    for rec in certs:
        if only_records is not None and rec.pk not in only_records:
            continue
        recipients.setdefault(rec.payload.get('document'), set()).add(rec.employee_id)
    for rec in certs:
        if only_records is not None and rec.pk not in only_records:continue
        if rec.payload.get('state') not in ('DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL','CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA'):
            continue
        candidates = certificate_candidates(rec,root=root)
        if len(candidates) != 1 or len(recipients.get(rec.payload.get('document'), set())) != 1:
            counts['ambiguous_certificate_bindings'] += 1
            continue
        t = candidates[0]
        source_path = next((s for s in rec.payload.get('sources', []) if ' :: ' not in s and s.lower().endswith('.pdf')), None)
        if source_path and any(token in norm(source_path).split() for token in ['modelo', 'modelos', 'template', 'templates']):
            issues.append({'record_id': rec.pk, 'reason': 'Modelo excluído da regularização'})
            counts['pdf_reading_issues'] += 1
            continue
        if not source_path:
            issues.append({'record_id': rec.pk, 'reason': 'PDF apenas em arquivo ou localização não inequívoca'})
            counts['pdf_reading_issues'] += 1
            continue
        root_path = Path(root).resolve()
        path = (root_path / source_path).resolve()
        if not path.is_relative_to(root_path):
            raise ValidationError('Caminho documental fora da pasta autorizada.')
        try:
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha != rec.payload['document']:
                raise ValidationError('O PDF mudou desde a conferência; associação suspensa.')
            validate_pdf(SimpleUploadedFile(path.name, data, content_type='application/pdf'))
        except (OSError, ValidationError) as exc:
            issues.append({'record_id': rec.pk, 'reason': str(exc)})
            counts['pdf_reading_issues'] += 1
            continue
        current_state = TrainingDocumentState.objects.filter(training=t).first()
        if current_state and current_state.issued is False:
            issues.append({'record_id': rec.pk, 'reason': 'PDF contradiz emissão explicitamente não confirmada; requer revisão'})
            counts['pdf_reading_issues'] += 1
            continue
        archive, created = HistoricalCertificatePDF.objects.get_or_create(training=t, sha256=sha, defaults={
            'source_record': rec, 'content': data, 'source_reference': source_path,
            'binding_evidence': {'employee_number': rec.employee.number, 'training_id': t.pk,
                'participation_id': (t.source_data or {}).get('ID Participação'), 'source_key': t.source_key,
                'method': 'IDENTIDADE_EPAL_ACAO_DATA_E_PARTICIPACAO_UNICA', 'declaration_id': rec.source_key}})
        if created:
            counts['pdfs_associated_created'] += 1
            AuditEvent.objects.create(actor=actor, action='HISTORICAL_PDF_ARCHIVED', detail=json.dumps({
                'archive_id': archive.pk, 'training_id': t.pk, 'sha256': sha, 'source': source_path,
                'binding': archive.binding_evidence}, ensure_ascii=False))
        else:
            counts['pdfs_existing'] += 1
        state, _ = TrainingDocumentState.objects.get_or_create(training=t, defaults={'reference': source_path[:500]})
        if state.issued is not True:
            state.issued = True
            state.save(update_fields=['issued'])
        counts['delivery_confirmations_created'] += confirm_delivery(t, actor, 'PDF final conferido SHA-256 ' + sha)
    return {**counts, 'issues': issues, **summary()}


def historical_delivery_inventory():
    """Map legacy and documentary confirmations to active participations once."""
    from .models import HistoricalInterfaceLink,HistoricalDocumentaryParticipation,PrivateDocument
    from .historical_review import active_pdfs
    from .certificate_register import RULE as folder_rule
    active_ids=set(Training.objects.values_list('pk',flat=True))
    legacy={};documentary={}
    for tid,old_ids,doc_ids in HistoricalInterfaceLink.objects.values_list('training_id','participation__legacy_training_ids','participation__documentary_participation_ids'):
        for pk in old_ids:legacy[pk]=tid
        for pk in doc_ids:documentary[pk]=tid
    delivered={legacy.get(pk,pk) for pk in TrainingDocumentState.objects.filter(historical_delivery_basis__in=[RULE,folder_rule],learner_delivered=True).values_list('training_id',flat=True)}
    delivered.update(documentary[pk] for pk in HistoricalDocumentaryParticipation.objects.filter(learner_delivered=True).values_list('pk',flat=True) if pk in documentary)
    delivered &= active_ids
    archived=set()
    for tid,did in active_pdfs().values_list('training_id','documentary_participation_id'):
        target=legacy.get(tid,tid) if tid else documentary.get(did)
        if target:archived.add(target)
    archived.update(legacy.get(pk,pk) for pk in PrivateDocument.objects.filter(kind='certificate',active=True).values_list('training_id',flat=True))
    return Training.objects.filter(pk__in=delivered),archived


def summary():
    from .historical_review import active_pdfs
    from .models import HistoricalDocumentaryParticipation
    archived=active_pdfs()
    documentary=HistoricalDocumentaryParticipation.objects.all()
    delivered,canonical_archived_ids = historical_delivery_inventory()
    archived_training_ids = archived.filter(training__isnull=False).values_list('training_id', flat=True)
    from .models import PrivateDocument
    legacy_pdf_ids = PrivateDocument.objects.filter(kind='certificate').values_list('training_id', flat=True)
    missing = delivered.exclude(pk__in=canonical_archived_ids)
    from .certificate_register import RULE as folder_rule
    source_ids=set(Training.objects.filter(source_data___certificate_summary__authority=folder_rule).values_list('pk',flat=True))
    latest_states={pk:resolution.get('state') for pk,resolution in DocumentaryDecision.objects.order_by('pk').values_list('record_id','resolution')}
    pending=sum(latest_states.get(pk)!='resolved' for pk in DocumentaryRecord.objects.filter(kind__in=['held_assertion','unlinked_test']).values_list('pk',flat=True))
    return {'historical_delivered_total': delivered.count(),
            'documentary_participations':documentary.count(),'operational_delivery_confirmations':delivered.count(), 'archived_pdf_total': archived.count(),
            'pdf_bindings_under_review':HistoricalCertificatePDF.objects.count()-archived.count(),
            'participations_with_archived_pdf': archived.filter(training__isnull=False).values('training_id').distinct().count()+archived.filter(documentary_participation__isnull=False).values('documentary_participation_id').distinct().count(),
            'delivered_pdf_missing': missing.count(), 'delivered_source_associated':delivered.filter(pk__in=source_ids).count(),
            'delivered_pdf_source_missing':delivered.exclude(pk__in=canonical_archived_ids|source_ids).count(), 'pending_assertions': pending,
            'certificate_associations_pending': DocumentaryRecord.objects.filter(kind='certificate_reference',
                state__in=['DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL','CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA']).exclude(pk__in=archived.values('source_record_id')).exclude(pk__in=[pk for pk,state in latest_states.items() if state=='resolved']).count(),
            'attendance_documentary_assertions': DocumentaryRecord.objects.filter(kind='attendance_assertion').count(),
            'original_test_documentary_assertions': DocumentaryRecord.objects.filter(kind__in=['original_test','original_test_reviewed']).count(),
            'contexts_with_missing_fields': sum(bool(missing_context_fields(r)) for r in DocumentaryRecord.objects.filter(kind='context').prefetch_related('decisions')),
            'source_assertions_preserved': DocumentaryRecord.objects.count(),
            'documents_to_rectify':DocumentaryRecord.objects.filter(kind='document_issue').count(),
            'academic_values_not_inferred': True}

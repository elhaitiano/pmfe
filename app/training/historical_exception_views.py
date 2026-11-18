from .professional_ui import person_ids
import hashlib
import io
import json
import zipfile
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, HttpResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from .models import DocumentaryRecord, DocumentaryDecision, HistoricalCertificatePDF, Training
from . import historical_regularization as service
from . import historical_review as review_service


@login_required
@require_http_methods(['GET'])
def source_certificate(request, sha256):
    require(request.user)
    snapshot=review_service.latest_snapshot()
    proof=next((p for p in snapshot.report['certificate_sources']+snapshot.report.get('image_certificate_sources',[]) if p['document']==sha256),None) if snapshot else None
    if not proof:raise Http404
    from pathlib import Path
    try:data=Path(proof['read_path']).read_bytes()
    except OSError:raise Http404('Leitura do documento indisponível.')
    if hashlib.sha256(data).hexdigest()!=sha256:
        return HttpResponse('O documento mudou desde a conferência; nova verificação necessária.',status=409)
    response=FileResponse(io.BytesIO(data),content_type='application/pdf',as_attachment=request.GET.get('view')!='1',
        filename='certificado-'+str(proof.get('number') or 'por-identificar')+'-'+sha256[:12]+'.pdf')
    response['Cache-Control']='private, no-store'
    response['Content-Security-Policy']="sandbox; default-src 'none'"
    response['X-Content-Type-Options']='nosniff'
    return response


@login_required
@require_http_methods(['GET'])
def source_document(request, pk, index):
    require(request.user)
    record=get_object_or_404(DocumentaryRecord,pk=pk)
    p=record.payload
    if p.get('claims'):
        items=[c.get('source',{}) for c in p['claims']]
    elif record.kind=='context':
        snapshot=review_service.latest_snapshot()
        items=snapshot.report.get('context_sources',{}).get(p.get('Contexto documental'),[]) if snapshot else []
    elif isinstance(p.get('sources'),list):
        items=[s if isinstance(s,dict) else {'source':s,'document':p.get('document')} for s in p['sources']]
    else:
        items=[{'source':p.get('source'),'document':p.get('document')}]
    if index>=len(items):raise Http404
    source=items[index]
    if source.get('financial_register') or source.get('preparatory_or_derived_or_synthetic_source'):
        raise PermissionDenied('Documento administrativo ou derivado fora da transferência de evidência.')
    from django.conf import settings
    from pathlib import Path
    root_value=getattr(settings,'PMF_CERTIFICATE_SOURCE_ROOT','')
    if not root_value or not source.get('source') or not source.get('document'):raise Http404
    root=Path(root_value).resolve();path=(root/source['source']).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in ['.pdf','.xlsx','.xlsm','.xls','.docx']:
        raise PermissionDenied
    from .certificate_evidence import io_path
    try:data=io_path(path).read_bytes()
    except OSError:raise Http404('Documento não disponível nesta localização.')
    if hashlib.sha256(data).hexdigest()!=source['document']:
        return HttpResponse('A fonte mudou; consulta suspensa até nova conferência.',status=409)
    import mimetypes
    response=FileResponse(io.BytesIO(data),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
        as_attachment=path.suffix.lower()!='.pdf' or request.GET.get('view')!='1',filename=path.name)
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    if path.suffix.lower()=='.pdf':response['Content-Security-Policy']="sandbox; default-src 'none'"
    return response


def require(user, edit=False):
    permission = 'training.change_documentaryrecord' if edit else 'training.view_documentaryrecord'
    if not (user.is_active and user.is_staff and user.has_perm(permission)):
        raise PermissionDenied


def describe(r):
    p = r.payload
    return p.get('course_reference') or p.get('course_literal') or p.get('Título literal') or p.get('source', '')


FIELD_NAMES = {'hours_per_day': 'horas/dia', 'days': 'duração em dias', 'start': 'data de início', 'end': 'data de fim', 'group': 'grupo', 'operational_binding': 'associação das horas ao registo operacional'}


def reason(r):
    if r.payload.get('divergence'):
        return r.payload['divergence'].get('reason','Horários ou cargas horárias divergentes')
    if r.payload.get('reason'):
        value=r.payload['reason']
        return {'IDENTIDADE_NAO_UNICA':'Nome da fonte sem correspondência única comprovada no cadastro EPAL.',
            'EDICAO_NAO_UNICA':'A fonte corresponde a mais de uma edição possível.',
            'ACAO_NAO_UNICA':'A acção da fonte necessita de identificação inequívoca.'}.get(value,value)
    if r.kind=='document_issue':return r.payload.get('reason','Documento por retificar')
    if r.kind == 'context':
        return 'Falta comprovar: ' + ', '.join(FIELD_NAMES[k] for k in service.missing_context_fields(r))
    if r.kind == 'certificate_reference':
        return 'Associação à participação ou arquivo PDF por concluir'
    if r.kind == 'unlinked_test':
        return 'Teste sem associação segura à participação'
    if service.has_conflicting_claims(r):
        return 'As fontes apresentam valores diferentes'
    if r.payload.get('tipo') == 'presença':
        return 'A presença não está comprovada por fonte de formação suficiente'
    return 'Falta comprovar a escala, data ou fonte do valor'


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def index(request):
    require(request.user, request.method == 'POST')
    error = None
    if request.method == 'POST':
        try:
            ids = [int(v) for v in request.POST.getlist('records')]
            service.decide(request.user, ids, request.POST.get('choice', ''), request.POST.get('reason', ''),
                           request.POST.get('evidence', ''), request.POST.get('signature', ''), hours=request.POST.get('hours'))
            return redirect('historical_exceptions')
        except (ValueError, ValidationError) as exc:
            error = str(exc)
    objects = DocumentaryRecord.objects.filter(kind__in=['context', 'held_assertion', 'unlinked_test', 'certificate_reference','document_issue']).select_related('employee').prefetch_related('decisions').order_by('pk')
    kind_filter = request.GET.get('kind', '')
    if kind_filter == 'conflicts':
        objects = objects.filter(kind__in=['held_assertion','context','document_issue'])
    elif kind_filter in ('context', 'certificate_reference', 'unlinked_test','document_issue'):
        objects = objects.filter(kind=kind_filter)
    elif kind_filter == 'missing':
        objects = objects.filter(kind='held_assertion')
    query = request.GET.get('q', '').strip()
    if query:
        objects = objects.filter(Q(employee_id__in=person_ids(query)) | Q(payload__icontains=query))
    entries = []
    archived = set(review_service.active_pdfs().values_list('source_record_id', flat=True))
    for r in objects:
        if kind_filter=='conflicts' and not service.has_conflicting_claims(r):continue
        if kind_filter=='missing' and service.has_conflicting_claims(r):continue
        if r.kind == 'context' and not service.missing_context_fields(r):
            continue
        last = service.latest_decision(r)
        if last and last.resolution.get('state') == 'resolved' and request.GET.get('scope') != 'all':
            continue
        if r.kind == 'certificate_reference':
            if r.state not in ('DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL','CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA') or r.pk in archived:
                continue
        entries.append({'record': r, 'course': describe(r), 'field': service.field_label(r), 'reason': reason(r),
                        'state': last.resolution.get('state') if last else 'pending'})
    entries.sort(key=lambda e: (0 if service.has_conflicting_claims(e['record']) else
        {'held_assertion': 1, 'unlinked_test': 2, 'certificate_reference': 3, 'context': 4}.get(e['record'].kind, 5), e['record'].pk))
    from .models import TrainingDocumentState
    from types import SimpleNamespace
    delivered,archived_ids=service.historical_delivery_inventory()
    missing_archive=[SimpleNamespace(training=t,training_id=t.pk) for t in delivered.exclude(pk__in=archived_ids).select_related('employee')]
    response = render(request, 'historical/exceptions.html', {'page': Paginator(entries, 30).get_page(request.GET.get('page')),
        'counts': service.summary(), 'pending_count': len(entries), 'error': error, 'query': query,
        'kind_filter': kind_filter,
        'missing_archive_rows': missing_archive, 'review_snapshot':review_service.latest_snapshot(),
        'review_groups':review_service.grouped_pending()})
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def detail(request, pk):
    require(request.user, request.method == 'POST')
    r = get_object_or_404(DocumentaryRecord.objects.select_related('employee'), pk=pk)
    error = None
    signature = service.rule_signature(r)
    if request.method == 'POST':
        try:
            ids = [int(v) for v in request.POST.getlist('records')] or [pk]
            if pk not in ids:
                raise ValidationError('O registo de referência deve pertencer ao lote.')
            service.decide(request.user, ids, request.POST.get('choice', ''), request.POST.get('reason', ''),
                           request.POST.get('evidence', ''), request.POST.get('signature', ''), hours=request.POST.get('hours'))
            return redirect('historical_exceptions')
        except (ValueError, ValidationError) as exc:
            error = str(exc)
    similar = []
    if r.kind in ('held_assertion', 'context'):
        for candidate in DocumentaryRecord.objects.filter(kind=r.kind).select_related('employee').prefetch_related('decisions'):
            last = service.latest_decision(candidate)
            if service.rule_signature(candidate) == signature and not (last and last.resolution.get('state') == 'resolved'):
                similar.append(candidate)
    claims = [{'index': i, 'value': {'PRESENTE_DECLARADO':'Presente (P)','AUSENTE_DECLARADO':'Ausente (A)'}.get(c.get('status'),c.get('status') or c.get('value')), 'scale': c.get('scale'),
               'source': c.get('source'), 'date': c.get('date')} for i, c in enumerate(r.payload.get('claims', []))]
    if not claims and r.kind=='original_test_reviewed':
        claims=[{'index':i,'value':s['literal_result']['value'],'scale':s['literal_result']['scale'],
            'date':s['literal_result'].get('exam_date_literal'),'source':{'source':s['source'],'document':s['document'],
            'sheet':s['sheet'],'row':s['row'],'cell':s['literal_result'].get('cell')}} for i,s in enumerate(r.payload.get('sources',[]))]
    latest=service.latest_decision(r)
    return render(request, 'historical/exception_detail.html', {'record': r, 'course': describe(r), 'field': service.field_label(r),
        'is_conflict':service.has_conflicting_claims(r),
        'claims': claims, 'raw': json.dumps(r.payload, ensure_ascii=False, indent=2), 'signature': signature,
        'can_decide':r.kind in ('context','held_assertion','unlinked_test','certificate_reference') and not (latest and latest.resolution.get('state')=='resolved'),
        'certificate_source_sha':r.payload.get('document') if r.kind in ('certificate_reference','document_issue') else None,
        'similar': similar[:100], 'decisions': r.decisions.all(), 'error': error,
        'missing_fields': [FIELD_NAMES[k] for k in service.missing_context_fields(r)] if r.kind == 'context' else [],
        'participants': DocumentaryRecord.objects.filter(kind='nominal', payload__event_id=r.payload.get('Contexto documental')).select_related('employee') if r.kind == 'context' else [],
        'trainings': Training.objects.filter(employee_id=r.employee_id) if r.kind in ('unlinked_test', 'certificate_reference') else []})


@login_required
@require_http_methods(['GET'])
def pdf(request, pk):
    require(request.user)
    document = get_object_or_404(HistoricalCertificatePDF.objects.select_related('training__employee','documentary_participation__employee'), pk=pk)
    data = bytes(document.content)
    if hashlib.sha256(data).hexdigest() != document.sha256:
        return HttpResponse('Falha na integridade do PDF.', status=409)
    employee=document.training.employee if document.training_id else document.documentary_participation.employee
    identifier=str(document.training_id) if document.training_id else 'DOC-'+str(document.documentary_participation_id)
    response = FileResponse(io.BytesIO(data), content_type='application/pdf', as_attachment=request.GET.get('view') != '1',
                            filename=f'EPAL-{employee.number}-{identifier}.pdf')
    response['Cache-Control'] = 'private, no-store'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@require_http_methods(['GET'])
def report(request):
    require(request.user)
    from .models import TrainingDocumentState, HistoricalDocumentaryParticipation
    rows = []
    for s in TrainingDocumentState.objects.filter(historical_delivery_basis=service.RULE).select_related('training__employee'):
        rows.append({'employee_number': s.training.employee.number, 'employee_name': s.training.employee.name,
            'training_id': s.training_id, 'participation_source_key': s.training.source_key, 'course': s.training.title,
            'learner_delivery': 'Entregue' if s.learner_delivered else 'Por confirmar',
            'delivery_date': s.learner_date.isoformat() if s.learner_date else None,
            'archive': 'PDF arquivado' if review_service.active_pdfs().filter(training_id=s.training_id).exists() or s.training.private_documents.filter(kind='certificate').exists() else 'PDF por arquivar',
            'hr_delivered': s.hr_delivered, 'hr_date': s.hr_date.isoformat() if s.hr_date else None, 'rule': service.RULE})
    for p in HistoricalDocumentaryParticipation.objects.filter(learner_delivered=True).select_related('employee'):
        rows.append({'employee_number':p.employee.number,'employee_name':p.employee.name,'documentary_participation_id':p.pk,
            'participation_source_key':p.source_key,'course':p.title,'learner_delivery':'Entregue','delivery_date':p.learner_date.isoformat() if p.learner_date else None,
            'archive':'PDF arquivado' if p.pdfs.exists() else 'PDF por arquivar','hr_delivered':p.hr_delivered,'hr_date':None,'rule':p.delivery_basis})
    pending = []
    archived = set(review_service.active_pdfs().values_list('source_record_id', flat=True))
    for r in DocumentaryRecord.objects.filter(kind__in=['context', 'held_assertion', 'unlinked_test', 'certificate_reference','document_issue']).select_related('employee').prefetch_related('decisions'):
        last = service.latest_decision(r)
        if last and last.resolution.get('state') == 'resolved':
            continue
        if r.kind == 'context' and not service.missing_context_fields(r):
            continue
        if r.kind == 'certificate_reference' and (r.state not in ('DECLARACAO_NATIVA_RECIPIENTE_EXATO_EPAL','CERTIFICADO_FINAL_IDENTIDADE_CORROBORADA') or r.pk in archived):
            continue
        pending.append({'id': r.pk, 'employee_number': r.employee.number if r.employee else None,
            'employee_name': r.employee.name if r.employee else None, 'course': describe(r), 'field': service.field_label(r),
            'reason': reason(r), 'source_values': r.payload, 'latest_resolution': last.resolution if last else None})
    snapshot=review_service.latest_snapshot()
    response = HttpResponse(json.dumps({'totals': service.summary(), 'certificate_deliveries': rows, 'pending_exceptions': pending,
        'review':snapshot.report if snapshot else None,'grouped_pending':review_service.grouped_pending(),
        'documentary_participations':list(HistoricalDocumentaryParticipation.objects.values('pk','employee__number','title','start','end','total_hours','declared_days','hours_per_day','completed','learner_delivered','learner_date'))}, ensure_ascii=False, indent=2,default=str), content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="regularizacao-historica.json"'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_http_methods(['GET'])
def review_workbook(request):
    require(request.user)
    from .historical_review_export import workbook
    response=FileResponse(io.BytesIO(workbook()),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,filename='EPAL-revisao-pendencias.xlsx')
    response['Cache-Control']='private, no-store'
    return response


def export_archive():
    data = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        for doc in review_service.active_pdfs().select_related('training__employee','documentary_participation__employee').order_by('pk'):
            content = bytes(doc.content)
            if hashlib.sha256(content).hexdigest() != doc.sha256:
                raise ValidationError('Integridade documental inválida; exportação suspensa.')
            employee=doc.training.employee if doc.training_id else doc.documentary_participation.employee
            identifier=str(doc.training_id) if doc.training_id else 'DOC-'+str(doc.documentary_participation_id)
            path = f'{employee.number}/{identifier}/{doc.sha256}.pdf'
            archive.writestr(path, content)
            manifest.append({'path': path, 'sha256': doc.sha256, 'employee_number': employee.number,
                'training_id': doc.training_id, 'documentary_participation_id':doc.documentary_participation_id,
                'binding_evidence': doc.binding_evidence, 'source_reference': doc.source_reference})
        archive.writestr('MANIFESTO.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return data.getvalue()


@login_required
@require_http_methods(['GET'])
def archive(request):
    require(request.user)
    response = FileResponse(io.BytesIO(export_archive()), content_type='application/zip', as_attachment=True, filename='EPAL-certificados-arquivados.zip')
    response['Cache-Control'] = 'private, no-store'
    return response


def profile(employee, year='', area=''):
    from .models import TrainingDocumentState
    states = TrainingDocumentState.objects.filter(training__employee=employee, learner_delivered=True).select_related('training')
    records = list(DocumentaryRecord.objects.filter(employee=employee).exclude(kind='context').prefetch_related('decisions').order_by('kind', 'pk'))
    records=[r for r in records if not (r.kind in ('unlinked_test','held_assertion') and service.latest_decision(r) and service.latest_decision(r).resolution.get('canonical_fact_id'))]
    context_titles = {p.get('Contexto documental'): p.get('Título literal') for p in
        DocumentaryRecord.objects.filter(kind='context').values_list('payload', flat=True)}
    labels = {'nominal': 'Registo de formação', 'attendance_assertion': 'Presença diária',
        'original_test': 'Teste na escala original', 'certificate_reference': 'Referência de certificado',
        'original_test_reviewed':'Teste na escala original — fontes conciliadas',
        'held_assertion': 'Dado por rever', 'unlinked_test': 'Teste sem participação associada'}
    state_labels = {'PRESENTE_DECLARADO': 'Presença registada', 'AUSENTE_DECLARADO': 'Ausência registada',
              'CONFLITO': 'Fontes discordantes', 'VALOR_LITERAL_CONCORDANTE': 'Valor original concordante'}
    for r in records:
        r.display_kind = labels.get(r.kind, 'Registo documental')
        r.display_course = describe(r)
        if r.kind == 'nominal':
            r.display_course = context_titles.get(r.payload.get('event_id'), 'Formação da fonte')
        r.display_value = state_labels.get(r.state, 'Consultar fonte / validação documental')
        if r.kind == 'held_assertion':
            r.display_value = 'Fontes discordantes' if service.has_conflicting_claims(r) else 'Informação por comprovar'
        if r.kind in ('original_test','original_test_reviewed'):
            r.display_value = str(r.payload.get('value')) + ' / ' + str(r.payload.get('scale'))
            r.display_kind += ' — ' + {'initial': 'inicial', 'final': 'final'}.get(r.payload.get('stage'), 'fase da fonte')
        r.display_date = r.payload.get('date') or r.payload.get('end_declared') or ''
        decision = service.latest_decision(r)
        if decision and decision.resolution.get('state') == 'resolved':
            claim = decision.resolution.get('selected_source_claim')
            if claim:
                value = claim.get('status') or claim.get('value')
                r.display_value = state_labels.get(value, str(value))
                if claim.get('scale'):
                    r.display_value += ' / ' + str(claim['scale'])
                r.display_kind = 'Valor regularizado por decisão documentada'
            elif decision.resolution.get('training_id'):
                r.display_kind = 'Teste associado à participação #' + str(decision.resolution['training_id'])
    parts=employee.documentary_participations.prefetch_related('pdfs').all()
    if year.isdecimal() and len(year)==4:parts=parts.filter(start__year=int(year))
    if area:parts=parts.none()
    return {'historical_document_rows': [{'training': s.training, 'delivered': s.learner_delivered,
        'date': s.learner_date, 'hr': s.hr_delivered, 'pdfs': review_service.active_pdfs().filter(training_id=s.training_id),
        'archived': review_service.active_pdfs().filter(training_id=s.training_id).exists() or s.training.private_documents.filter(kind='certificate').exists()} for s in states],
        'documentary_records': records, 'certificate_backed_participations':parts}

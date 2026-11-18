"""Certificate evidence is distinct from attendance and from the PDF archive.

Uses the existing imported-evidence JSON, audit and snapshot mechanisms. No PDF
copies are made: source paths are hash checked on every consultation.
"""
import hashlib
import io
import json
import zipfile
from datetime import date
from decimal import Decimal
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.contrib.auth.decorators import login_required
from .certificate_evidence import io_path

RULE = 'EPAL_CERTIFICADOS_DUAS_ORIGENS_20261106'
DECLARATION = ('O responsável pelo processo confirmou que os certificados finais emitidos '
    'encontrados nas duas pastas Cerificados On Time foram entregues aos respectivos formandos. '
    'Confirmação histórica de 06/11/2026; não presume data de entrega nem entrega aos RH, '
    'notas ou horas de presença. As datas de realização e horas certificadas legíveis '
    'prevalecem quando a edição está comprovada.')

def read_source(location):
    path, separator, member = location.partition('::')
    raw = io_path(path).read_bytes()
    if separator:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            raw = archive.read(member)
    return raw

def source_bytes(document):
    if document.get('archive_id'):
        from .historical_review import active_pdfs
        archived=active_pdfs().filter(pk=document['archive_id'],sha256=document['sha256']).first()
        if archived:
            raw=bytes(archived.content)
            if hashlib.sha256(raw).hexdigest()==document['sha256']:return raw
    errors=[]
    for location in document['paths']:
        try:
            raw=read_source(location)
            if hashlib.sha256(raw).hexdigest()==document['sha256']:return raw
            errors.append('Conteúdo alterado: '+location)
        except (OSError,KeyError,zipfile.BadZipFile) as exc:errors.append(str(exc))
    raise ValueError('; '.join(errors) or 'Documento sem origem legível')

def reuse_archived_hours():
    """Retain previously validated archive evidence in the common indicators."""
    from .models import Training,HistoricalInterfaceLink,AuditEvent,HistoricalReviewSnapshot
    from .historical_review import active_pdfs,latest_snapshot
    from .certificate_evidence import certificate_facts
    from pypdf import PdfReader
    links=list(HistoricalInterfaceLink.objects.select_related('training','participation'))
    current={l.training_id:l.training for l in links};legacy={i:l.training for l in links for i in l.participation.legacy_training_ids};documentary={i:l.training for l in links for i in l.participation.documentary_participation_ids}
    candidates={}
    for pdf in active_pdfs():
        t=current.get(pdf.training_id) or legacy.get(pdf.training_id) or documentary.get(pdf.documentary_participation_id)
        if not t or not t.delivery_reported or t.validated_certificates:continue
        if str(pdf.binding_evidence.get('employee_number'))!=t.employee.number:continue
        raw=bytes(pdf.content)
        if hashlib.sha256(raw).hexdigest()!=pdf.sha256:continue
        try:facts=certificate_facts([p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages])
        except Exception:continue
        if not facts['certificate_front'] or facts['course_period']!=[str(t.start),str(t.end)] or not facts['total_hours']:continue
        candidates.setdefault(t.pk,[]).append({'archive_id':pdf.pk,'sha256':pdf.sha256,'paths':[pdf.source_reference],'facts':facts,'association':'Associação anterior validada por cadastro e período; conteúdo arquivado conferido por SHA-256'})
    changes=[]
    with transaction.atomic():
        for pk,documents in candidates.items():
            hours={d['facts']['total_hours'] for d in documents}
            if len(hours)!=1:continue
            t=Training.objects.get(pk=pk);h=next(iter(hours))
            record={'key':'participation-'+str(t.pk),'number':t.certificate_reference or None,'title':t.title,'start':str(t.start),'end':str(t.end),'hours':h,'issue_date':str(t.certificate_issue_date) if t.certificate_issue_date else None,'delivery_date':str(t.certificate_delivery_date) if t.certificate_delivery_date else None,'delivered':True,'authority':'EPAL_PREVIOUS_VALIDATED_ARCHIVE_20261106','documents':documents}
            t.source_data={**t.source_data,'_validated_certificates':[record],'_certificate_summary':{'delivered_count':1,'issued_count':int(t.certificate_issued is True),'hours':h,'hours_records':1,'authority':record['authority']}}
            t.version+=1;stamp_projection(t);t.save(update_fields=['source_data','version'])
            link=HistoricalInterfaceLink.objects.get(training=t);p=link.participation;p.evidence={**p.evidence,'validated_certificates':[record]};p.save(update_fields=['evidence'])
            change={'training_id':pk,'number':t.employee.number,'hours':h,'documents':documents,'delivery_state_preserved':True};changes.append(change)
            AuditEvent.objects.create(actor='Conferência documental EPAL',action='CERTIFICATE_ARCHIVED_HOURS_REUSE',detail=json.dumps(change,ensure_ascii=False))
        if changes:
            snapshot=latest_snapshot();payload=dict(snapshot.report);report=dict(payload['certificate_folder_regularization'])
            report['previous_archive_reused']={'certificates':len(changes),'hours':str(sum(Decimal(c['hours']) for c in changes)),'changes':changes,'new_delivery_confirmations':0}
            from .analytics import statistics
            report['after_statistics']={k:str(v) for k,v in statistics(Training.objects.all()).items()};payload['certificate_folder_regularization']=report
            HistoricalReviewSnapshot.objects.create(key=RULE+'_PREVIOUS_ARCHIVE',actor='Conferência documental EPAL',report=payload)
    return changes

def row_summary(row):
    return (row.indicator_source_data or {}).get('_certificate_summary') or {}

def stamp_projection(row):
    """Evidence imports are already reconciled, not pending manual user edits."""
    from .interface_sync import inputs
    facts=(row.source_data or {}).get('_EPAL_CONSOLIDATED_FACTS')
    if facts is None:return
    facts={**facts,'inputs':inputs(row),'sync_version':row.version}
    if row.validated_certificates:facts.update(completed=True,historical_delivery_confirmed=True)
    row.source_data={**row.source_data,'_EPAL_CONSOLIDATED_FACTS':facts}

def certificate_statistics(rows):
    rows=list(rows)
    return {
        'certificates_reported':sum(row_summary(r).get('delivered_count',int(r.delivery_reported)) for r in rows),
        'certificates_issued':sum(row_summary(r).get('issued_count',int(r.certificate_issued is True)) for r in rows),
        'certificate_holders':len({r.employee_id for r in rows if row_summary(r).get('delivered_count',int(r.delivery_reported))>0}),
        'certified_hours':sum((Decimal(str(row_summary(r)['hours'])) for r in rows if row_summary(r).get('hours') is not None),Decimal(0)),
        'certified_hours_records':sum(row_summary(r).get('hours_records',0) for r in rows),
    }

@login_required
def source_pdf(request,pk,sha256):
    from .historical_exception_views import require
    from .models import Training
    from django.shortcuts import get_object_or_404
    require(request.user)
    row=get_object_or_404(Training,pk=pk)
    documents=[d for c in row.validated_certificates for d in c['documents']]
    document=next((d for d in documents if d['sha256']==sha256),None)
    if not document:raise Http404
    try:raw=source_bytes(document)
    except ValueError as exc:return HttpResponse('Fonte indisponível ou alterada; nova conferência necessária. '+str(exc),status=409,content_type='text/plain; charset=utf-8')
    response=FileResponse(io.BytesIO(raw),content_type='application/pdf',as_attachment=request.GET.get('view')!='1',filename=f'certificado-{row.employee.number}-{sha256[:12]}.pdf')
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff';response['Content-Security-Policy']="sandbox; default-src 'none'"
    return response

@login_required
def folder_review(request):
    from .historical_exception_views import require
    from .historical_review import latest_snapshot
    from django.shortcuts import render
    from django.core.paginator import Paginator
    require(request.user)
    snapshot=latest_snapshot();report=snapshot.report.get('certificate_folder_regularization',{}) if snapshot else {}
    entries=[{**entry,'candidate_values':entry.get('values')} for entry in report.get('field_exceptions',[])+report.get('exceptions',[])+report.get('unmatched_identities',[])]
    if request.GET.get('export')=='1':
        from openpyxl import Workbook
        book=Workbook();sheet=book.active;sheet.title='Resumo'
        for k,v in report.get('counts',{}).items():sheet.append([k,v])
        sheet=book.create_sheet('Certificados associados');sheet.append(['Nome','N.º mecanográfico','Participação','Formação','Início','Fim','Horas certificadas','Emissão','Entrega','Data de entrega','PDFs / origens'])
        for c in report.get('changes',[]):
            v=c['certificate'];sheet.append([c['name'],c['number'],c['training_id'],v['title'],v['start'],v['end'],v['hours'],v['issue_date'],'Entregue',v['delivery_date'],'\n'.join(p for d in v['documents'] for p in d['paths'])])
        sheet=book.create_sheet('Excepções');sheet.append(['Nome / candidatos','Documento','Motivo','Dado necessário','Origens','Outras participações','Valores de emissão','Formação no certificado','Período certificado'])
        for e in entries:sheet.append([', '.join(e.get('names',[])),e.get('sha256'),e['reason'],e.get('needed','Número mecanográfico e identificação da edição/grupo correspondente'), '\n'.join(e.get('paths',[e.get('path','')])),json.dumps(e.get('other_course_matches',[]),ensure_ascii=False),json.dumps(e.get('values',[])),e.get('facts',{}).get('course_title_literal'),' / '.join(e.get('facts',{}).get('course_period') or [])])
        for sheet in book:
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value,str) and cell.value.startswith(('=','+','-','@','\t','\r','\n')):cell.value="'"+cell.value
        output=io.BytesIO();book.save(output)
        return HttpResponse(output.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="EPAL_certificados_duas_origens.xlsx"'})
    return render(request,'historical/certificate_folder_review.html',{'batch':report,'entries':Paginator(entries,30).get_page(request.GET.get('page')),'changes':Paginator(report.get('changes',[]),25).get_page(request.GET.get('certpage')),'entry_count':len(entries)})

@login_required
def pending_source_pdf(request,sha256):
    from .historical_exception_views import require
    from .historical_review import latest_snapshot
    require(request.user)
    snapshot=latest_snapshot();batch=snapshot.report.get('certificate_folder_regularization',{}) if snapshot else {}
    record=next((e for e in batch.get('field_exceptions',[])+batch.get('exceptions',[])+batch.get('unmatched_identities',[]) if e.get('sha256')==sha256),None)
    if not record:raise Http404
    try:raw=source_bytes(record)
    except ValueError:return HttpResponse('Origem indisponível ou alterada.',status=409)
    response=FileResponse(io.BytesIO(raw),content_type='application/pdf',as_attachment=request.GET.get('view')!='1',filename='certificado-por-associar-'+sha256[:12]+'.pdf')
    response['Cache-Control']='private, no-store';response['Content-Security-Policy']="sandbox; default-src 'none'";response['X-Content-Type-Options']='nosniff'
    return response

def apply_manifest(manifest):
    from .models import (Training,TrainingDocumentState,AuditEvent,HistoricalInterfaceLink,
        HistoricalReviewSnapshot,HistoricalDeliveryDirective)
    from .historical_review import latest_snapshot
    from .analytics import statistics
    def fingerprint(certificates):
        values=sorted((c['training_id'],sorted([(v['sha256'],v['facts']) for v in c['versions']],key=lambda v:v[0])) for c in certificates)
        return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    signature=fingerprint(manifest['safe_certificates'])
    key=RULE+'_'+signature[:12]
    # Earlier manifests used traversal order. Comparing the evidence identity
    # preserves those audit entries when a second traversal changes ordering.
    # Prefer the latest matching report, which may include later archive facts.
    for snapshot in HistoricalReviewSnapshot.objects.filter(key__startswith=RULE).only('report').order_by('-pk'):
        prior=snapshot.report.get('certificate_folder_regularization',{})
        identity=[{'training_id':c['training_id'],'versions':c['certificate']['documents']} for c in prior.get('changes',[])]
        if fingerprint(identity)==signature:return prior
    latest=latest_snapshot()
    inherited=latest.report.get('certificate_folder_regularization',{}) if latest else {}
    prior_by_id={c['training_id']:c for c in inherited.get('changes',[])}
    current_by_id={c['training_id']:c for c in manifest['safe_certificates']}
    incremental=bool(prior_by_id) and all(pk in current_by_id and fingerprint([{'training_id':pk,'versions':c['certificate']['documents']}])==fingerprint([current_by_id[pk]]) for pk,c in prior_by_id.items())
    work=[c for c in manifest['safe_certificates'] if not incremental or c['training_id'] not in prior_by_id]
    # Check every accepted source before writing anything. Paths remain outside the archive.
    for certificate in manifest['safe_certificates']:
        for document in certificate['versions']:
            source_bytes(document)
            for proof in document.get('identity_proofs',[]):source_bytes(proof)
        proof=certificate.get('version_resolution')
        if proof:
            from pathlib import Path
            roster=json.loads((Path(__file__).resolve().parents[3]/'entregaveis/consolidacao-formandos-20261105/CADASTRO_OFICIAL.json').read_text(encoding='utf8'))
            root=Path(roster['source']['path']).parent.parent
            valid=False
            for source in proof['sources']:
                try:
                    if hashlib.sha256(io_path(root/source['source']).read_bytes()).hexdigest()==source['document']:valid=True;break
                except OSError:pass
            if not valid:raise ValueError('Fonte nativa de desempate indisponível ou alterada: '+str(certificate['training_id']))
    counts={'confirmed_deliveries':0,'already_delivered':0,'certificates':0,'date_corrections':0,'certified_hours_recorded':0,'programme_hours_different':0,'versions_resolved':0,'documentary_associations_resolved':0,'repeated_delivery_counts_removed':0}
    changed_people=set();changes=[];edition_proposals={};field_exceptions=[];duplicates=[]
    if incremental:
        counts.update(inherited['counts']);changes=list(inherited['changes']);field_exceptions=list(inherited.get('field_exceptions',[]));duplicates=list(inherited.get('duplicate_counts',[]))
        from django.db.models import Q
        changed_people=set(Training.objects.filter(Q(source_data___certificate_summary__authority=RULE)|Q(source_data___certificate_calendar__rule=RULE)).values_list('employee_id',flat=True))
    with transaction.atomic():
        HistoricalDeliveryDirective.objects.get_or_create(key=RULE,defaults={'declaration':DECLARATION,'actor':'Responsável pelo processo EPAL','scope':{'origins':[c['origin'] for c in manifest['counts']],'no_delivery_date_inferred':True,'no_rh_delivery_inferred':True}})
        before_stats=statistics(Training.objects.all())
        for item in work:
            t=Training.objects.select_for_update().get(pk=item['training_id'])
            v=item['versions'];f=item['facts'];start,end=map(date.fromisoformat,f['course_period'])
            if end>date.today() or not all(x['facts']['completion_literal'] for x in v):raise ValueError('Certificado não concluído / período futuro: '+str(t.pk))
            if t.employee.number!=item['number']:raise ValueError('Identidade mudou depois da pré-visualização')
            expected=v[0].get('before')
            if expected and (str(t.start),str(t.end),t.certified)!=(expected['start'],expected['end'],expected['certified']):raise ValueError('Participação alterada depois da pré-visualização: '+str(t.pk))
            before={k:str(getattr(t,k)) if getattr(t,k) is not None else None for k in ('start','end','status','certified','certificate_issued','certificate_reference','certificate_issue_date','certificate_delivery_date')}
            dates_changed=(t.start,t.end)!=(start,end)
            counts['date_corrections']+=dates_changed
            counts['already_delivered' if t.delivery_reported else 'confirmed_deliveries']+=1
            if f['total_hours'] is not None:
                counts['certified_hours_recorded']+=1
                counts['programme_hours_different']+=t.planned_hours is not None and t.planned_hours!=Decimal(f['total_hours'])
            counts['versions_resolved']+=bool(item.get('version_resolution'))
            issue_dates={x['facts']['issue_date'] for x in v if x['facts']['issue_date'] and end.isoformat()<=x['facts']['issue_date']<=date.today().isoformat()}
            if len(issue_dates)!=1:
                field_exceptions.append({'training_id':t.pk,'names':[t.employee.name],'sha256':v[0]['sha256'],'paths':v[0]['paths'],'reason':'Data de emissão ausente, inválida ou divergente entre versões; preservado o valor existente','needed':'Comprovar a data de emissão, sem voltar a confirmar a entrega','values':[x['facts']['issue_date'] for x in v]})
            record={'key':'participation-'+str(t.pk),'number':f.get('certificate_number'),'title':f['course_title_literal'],
                'start':str(start),'end':str(end),'hours':f['total_hours'],'issue_date':next(iter(issue_dates)) if len(issue_dates)==1 else None,
                'delivered':True,'delivery_date':str(t.certificate_delivery_date) if t.certificate_delivery_date else None,
                'authority':RULE,'documents':[{'sha256':x['sha256'],'paths':x['paths'],'facts':x['facts'],'association':x['method'],'identity_evidence':x.get('identity_proofs',[])} for x in v],
                'rejected_versions':item.get('rejected_versions',[]),'version_resolution':item.get('version_resolution')}
            t.start=start;t.end=end;t.certified=True;t.certificate_issued=True;t.status='Concluído'
            if record['issue_date']:t.certificate_issue_date=date.fromisoformat(record['issue_date'])
            if record['number']:t.certificate_reference=record['number']
            t.source_data={**t.source_data,'_validated_certificates':[record],
                '_certificate_summary':{'delivered_count':1,'issued_count':1,'hours':record['hours'],'hours_records':int(record['hours'] is not None),'authority':RULE}}
            t.version+=1;stamp_projection(t);t.save(update_fields=['start','end','certified','certificate_issued','status','certificate_issue_date','certificate_reference','source_data','version'])
            state,_=TrainingDocumentState.objects.get_or_create(training=t,defaults={'reference':RULE})
            state_before={k:getattr(state,k) for k in ('issued','learner_delivered','learner_responsible','historical_delivery_basis','revision')}
            state.issued=True;state.learner_delivered=True;state.learner_responsible=state.learner_responsible or 'Responsável pelo processo EPAL'
            state.historical_delivery_basis=state.historical_delivery_basis or RULE;state.reference=state.reference or RULE
            # Preserve all pre-existing dates and the independent RH state.
            state.revision+=1;state.save()
            link=HistoricalInterfaceLink.objects.select_related('participation__edition').filter(training=t).first()
            if link:
                p=link.participation;p.completed=True;p.evidence={**p.evidence,'validated_certificates':[record]};p.save(update_fields=['completed','evidence'])
                if dates_changed:edition_proposals.setdefault(p.edition_id,set()).add((start,end))
            after={k:str(getattr(t,k)) if getattr(t,k) is not None else None for k in before}
            change={'training_id':t.pk,'employee_id':t.employee_id,'number':t.employee.number,'name':t.employee.name,'before':before,'after':after,'certificate':record}
            AuditEvent.objects.create(actor='Responsável pelo processo EPAL',action='CERTIFICATE_FOLDER_REGULARIZATION',detail=json.dumps({'rule':RULE,'declaration':DECLARATION,'previous_document_state':state_before,**change},ensure_ascii=False))
            counts['certificates']+=1;changed_people.add(t.employee_id);changes.append(change)
        from .models import DocumentaryRecord,DocumentaryDecision
        from .historical_regularization import latest_decision,rule_signature
        document_links={d['sha256']:c for c in changes for d in c['certificate']['documents']}
        for source in DocumentaryRecord.objects.filter(kind='certificate_reference',payload__document__in=list(document_links)).prefetch_related('decisions'):
            change=document_links[source.payload['document']]
            if source.employee_id!=change['employee_id']:continue
            previous=latest_decision(source)
            if previous and previous.resolution.get('state')=='resolved':continue
            DocumentaryDecision.objects.create(record=source,actor='Responsável pelo processo EPAL',reason='Certificado final associado à participação por documento, identidade e edição comprovados; entrega confirmada pela orientação do responsável.',evidence=json.dumps(change['certificate']['documents'],ensure_ascii=False),rule_signature=rule_signature(source),resolution={'state':'resolved','training_id':change['training_id'],'rule':RULE,'archive_deferred':True,'delivery_confirmed':True})
            counts['documentary_associations_resolved']+=1
        # A manual projection of the same named examination can repeat an already
        # certified participation. Deduplicate its certificate count only when
        # identity, course, period AND the examination's explicit group agree.
        from .certificate_evidence import normalize
        import re
        masters={c['training_id']:c for c in changes}
        for t in Training.objects.filter(certified=True,interface_link__participation__edition__source_key__startswith='manual:').select_related('interface_link__participation__edition'):
            if t.pk in masters or t.validated_certificates:continue
            facts=t.source_data.get('_EPAL_CONSOLIDATED_FACTS',{})
            if facts.get('archived_pdf_ids'):continue
            matches=[]
            for master in changes:
                if master['employee_id']!=t.employee_id:continue
                primary=Training.objects.get(pk=master['training_id'])
                group=primary.source_data.get('_EPAL_CONSOLIDATED_FACTS',{}).get('group')
                if not group or not primary.course_id or primary.course_id!=t.course_id or (primary.start,primary.end)!=(t.start,t.end) or normalize(primary.title)!=normalize(t.title):continue
                if t.certificate_reference and t.certificate_reference!=primary.certificate_reference:continue
                sources=[claim.get('source',{}) for g in facts.get('grades',[]) for claim in g.get('claims',[])]
                proof=[s for s in sources if re.search(r'\bgrupo\s*0?'+re.escape(str(group))+r'\b',normalize(s.get('source','')+' '+s.get('sheet',''))) and not s.get('financial_register') and not s.get('preparatory_or_derived_or_synthetic_source')]
                if proof:matches.append((master,proof))
            if len(matches)!=1:continue
            master,proof=matches[0];record=master['certificate']
            t.source_data={**t.source_data,'_validated_certificates':[record],
                '_certificate_summary':{'delivered_count':0,'issued_count':0,'hours':None,'hours_records':0,'display_hours':record['hours'],'same_certificate_as':master['training_id'],'authority':RULE}}
            t.version+=1;stamp_projection(t);t.save(update_fields=['source_data','version'])
            duplicate={'training_id':t.pk,'same_certificate_as':master['training_id'],'number':t.employee.number,'proof':proof,'delivery_state_and_date_preserved':True,'previous_certificate_count':1,'new_certificate_count':0}
            AuditEvent.objects.create(actor='Conferência documental EPAL',action='CERTIFICATE_DUPLICATE_COUNT',detail=json.dumps({'rule':RULE,**duplicate},ensure_ascii=False))
            duplicates.append(duplicate);counts['repeated_delivery_counts_removed']+=1;changed_people.add(t.employee_id)
        # Edition calendars may change only when certificate proposals agree and no
        # participant in that edition would be left on a different calendar.
        from .models import ConsolidatedEdition,TrainingGroup
        for eid,proposals in edition_proposals.items():
            if len(proposals)!=1:continue
            start,end=next(iter(proposals));edition=ConsolidatedEdition.objects.get(pk=eid)
            peers=list(Training.objects.filter(interface_link__participation__edition_id=eid))
            if peers:
                for peer in peers:
                    if (peer.start,peer.end)!=(start,end):
                        old_period=[str(peer.start),str(peer.end)];peer.start=start;peer.end=end;peer.version+=1
                        peer.source_data={**peer.source_data,'_certificate_calendar':{'rule':RULE,'previous':old_period,'edition':eid}}
                        stamp_projection(peer);peer.save(update_fields=['start','end','version','source_data']);counts['date_corrections']+=1;changed_people.add(peer.employee_id)
                        AuditEvent.objects.create(actor='Responsável pelo processo EPAL',action='CERTIFICATE_EDITION_CALENDAR',detail=json.dumps({'training_id':peer.pk,'edition':eid,'before':old_period,'after':[str(start),str(end)],'rule':RULE},ensure_ascii=False))
                old_dates=[str(edition.start),str(edition.end)];edition.start=start;edition.end=end
                edition.evidence={**edition.evidence,'certificate_calendar_confirmation':{'rule':RULE,'previous':old_dates,'period':[str(start),str(end)]}}
                edition.save(update_fields=['start','end','evidence'])
                for gid in {t.group_id for t in peers if t.group_id}:
                    group=TrainingGroup.objects.get(pk=gid)
                    group.start=start;group.end=end;group.save(update_fields=['start','end'])
        # Repair tracking for evidence committed by earlier versions of this
        # importer, before a later POST could mistake it for a manual edit.
        from django.db.models import Q
        repaired=[]
        for t in Training.objects.filter(Q(source_data___certificate_summary__authority=RULE)|Q(source_data___certificate_calendar__rule=RULE)):
            before=t.source_data;stamp_projection(t)
            if t.source_data!=before:
                Training.objects.filter(pk=t.pk).update(source_data=t.source_data);repaired.append({'training_id':t.pk,'previous_tracking':before.get('_EPAL_CONSOLIDATED_FACTS',{}).get('inputs')})
        if repaired:AuditEvent.objects.create(actor='Conferência documental EPAL',action='CERTIFICATE_PROJECTION_TRACKING',detail=json.dumps({'rule':RULE,'tracking_only':True,'repairs':repaired},ensure_ascii=False))
        counts['learners_updated']=len(changed_people)
        report={'rule':RULE,'counts':counts,'origins':manifest['counts'],'distinct_pdf_contents':manifest['unique_pdf_contents'],
            'final_pdf_versions':manifest['final_pdf_versions'],'exceptions':manifest['exceptions'],'unmatched_identities':manifest['excluded'],
            'read_errors':manifest['read_errors'],'field_exceptions':field_exceptions,'duplicate_counts':duplicates,'changes':changes,'before_statistics':inherited['before_statistics'] if incremental else {k:str(v) for k,v in before_stats.items()},
            'after_statistics':{k:str(v) for k,v in statistics(Training.objects.all()).items()},'archive_copies_deferred':True}
        snapshot=latest_snapshot();payload=dict(snapshot.report) if snapshot else {};payload['certificate_folder_regularization']=report
        HistoricalReviewSnapshot.objects.create(key=key,actor='Responsável pelo processo EPAL',report=payload)
    return report

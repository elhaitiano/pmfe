"""Private, formula-free lists of the review and current unresolved evidence."""
import io
import json
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from . import historical_review as review
from . import historical_regularization as service
from .models import DocumentaryRecord, TrainingDocumentState


def workbook():
    snapshot=review.latest_snapshot()
    report=snapshot.report if snapshot else {}
    grouped=review.grouped_pending()
    book=Workbook();book.remove(book.active)
    def sheet(title, headers, rows):
        tab=book.create_sheet(title);tab.append(headers)
        for row in rows:
            tab.append([json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list)) else v for v in row])
        for row in tab:
            for cell in row:
                if isinstance(cell.value,str):cell.data_type='s'
        for c in tab[1]:c.font=Font(bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='173B59')
        tab.freeze_panes='A2';tab.auto_filter.ref=tab.dimensions
        for col in tab.columns:
            tab.column_dimensions[col[0].column_letter].width=min(65,max(16,len(str(col[0].value))+3))
    sheet('Resumo',['Indicador','Valor'],list(service.summary().items())+[(k,v) for k,v in grouped.items() if k!='groups'])
    sheet('78 entregas iniciais',['Mecanográfico','Formando','Formação','Participação','Início','Fim','Número certificado','PDF localizado','PDFs'],[
        [r.get(k) for k in ['number','name','course','training_id','start','end','certificate_number','resolved','pdfs']] for r in report.get('initial_78',[])])
    from types import SimpleNamespace
    delivered,archived_ids=service.historical_delivery_inventory()
    missing=[SimpleNamespace(training=t,training_id=t.pk) for t in delivered.exclude(pk__in=archived_ids).select_related('employee')]
    sheet('PDFs por arquivar agora',['Mecanográfico','Formando','Formação','Participação','Início','Fim','Estado entrega','Arquivo'],[
        [s.training.employee.number,s.training.employee.name,s.training.title,s.training_id,s.training.start.isoformat(),s.training.end.isoformat(),'Entregue','PDF por arquivar'] for s in missing])
    sheet('134 associações iniciais',['Mecanográfico','Formando','Formação','Registo documental','Resolvido','SHA256','PDFs'],[
        [r.get(k) for k in ['number','name','course','record_id','resolved','document','pdfs']] for r in report.get('initial_134',[])])
    sheet('171 PDFs iniciais',['Mecanográfico','Formando','Formação','Participação','Arquivo PDF','Formato','Relação entre versões','Revisão','Factos da fonte'],[
        [r.get(k) for k in ['number','name','course','training_id','archive_id']]+[r.get('facts',{}).get('layout'),r.get('relationship'),r.get('binding_review'),r.get('facts')] for r in report.get('archive_initial',[])])
    sheet('Pendências agrupadas',['Formação','Problema','Fonte SHA256','Localizações','Ocorrências','Formandos distintos','Participações identificadas','Registos','Mecanográficos','Fonte repetida'],[
        [g.get(k) for k in ['course','problem','source_document','sources','occurrences','distinct_learners','distinct_identified_participations','record_ids','numbers','same_binary_source_multiple_locations']] for g in grouped['groups']])
    pending_ids={pk for g in grouped['groups'] for pk in g['record_ids']}
    sheet('Decisões e fontes em falta',['Registo','Mecanográfico','Formando','Tipo','Campo','Estado da fonte','Valores e referências originais','Decisão mais recente'],[
        [r.pk,r.employee.number if r.employee else None,r.employee.name if r.employee else None,r.kind,service.field_label(r),r.state,r.payload,
         service.latest_decision(r).resolution if service.latest_decision(r) else None]
        for r in DocumentaryRecord.objects.filter(pk__in=pending_ids).select_related('employee').prefetch_related('decisions').order_by('pk')])
    sheet('Avaliações conciliadas',['Dados documentais sem conversão /20'],[[r] for r in report.get('canonical_tests',[])])
    sheet('Fontes dos certificados',['Mecanográfico','Formando','SHA256','Localizações','Factos','Ressalvas'],[
        [r.get('number'),r.get('name'),r.get('document'),r.get('sources'),r.get('facts'),r.get('quality_flags')] for r in report.get('certificate_sources',[])])
    data=io.BytesIO();book.save(data);return data.getvalue()

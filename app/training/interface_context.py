from .models import Training,HistoricalCertificatePDF


def archived_documents(request):
    if not getattr(request,'user',None) or not request.user.is_staff:return {}
    match=getattr(request,'resolver_match',None)
    if not match:return {}
    employee_views={'employee_data','employee_detail','employee_documents','employee_print'}
    training_views={'certificate_edit','evaluation_detail','training_edit'}
    if match.url_name in employee_views:rows=Training.objects.filter(employee_id=match.kwargs.get('pk'))
    elif match.url_name in training_views:rows=Training.objects.filter(pk=match.kwargs.get('pk'))
    else:return {}
    ids={i for data in rows.values_list('source_data',flat=True) for i in data.get('_EPAL_CONSOLIDATED_FACTS',{}).get('archived_pdf_ids',[])}
    return {'existing_historical_pdfs':HistoricalCertificatePDF.objects.filter(pk__in=ids).defer('content').select_related('training__employee','documentary_participation__employee')}

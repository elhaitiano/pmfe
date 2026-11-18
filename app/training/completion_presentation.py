"""Compatibility entry point for the shared read-only policy."""
from . import operational_status as policy
def summary(employee_id):
    items=policy.rows(employee_id=employee_id);metrics=policy.summary(items)
    for r in items:r['pending_reason']=' '.join(r['reasons'])
    return {'items':items,'totals':metrics,'operational_summary':metrics}
def context(request):
    match=getattr(request,'resolver_match',None)
    if request.user.is_authenticated and match and match.url_name in ['employee_data','employee_detail','employee_print'] and match.kwargs.get('pk'):
        data=summary(match.kwargs['pk'])
        return {'completion_certificates':data,'operational_summary':data['totals']}
    return {}

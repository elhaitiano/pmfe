"""Per-link return paths: no shared session state, data writes or permission bypass."""
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from django.urls import resolve,reverse,Resolver404
from django.utils.http import url_has_allowed_host_and_scheme
from django.core import signing
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect,render
from django.views.decorators.http import require_GET

SALT='pmfe.saved-navigation.v1'
ROOTS={'home','group_detail','consolidated_edition','course_groups','group_validation','evaluation_index',
       'direct_assessments','employee_data_select','history_index','consolidated_history','validation_index','consultations','participation_executive'}
DETAILS={'employee_data','employee_detail','employee_summary','evaluation_detail','historical_detail','consolidated_participation'}
EDITS={'training_edit','employee_edit','evaluation_edit','reaction_edit','validation_decision','group_validation'}

def safe_return(request,value):
    if not value or len(value)>4096 or any(ord(c)<32 for c in value) or '\\' in value:return ''
    if not url_has_allowed_host_and_scheme(value,{request.get_host()},require_https=request.is_secure()):return ''
    parsed=urlsplit(value)
    if not parsed.path.startswith('/') or parsed.path.startswith('//'):return ''
    try:match=resolve(parsed.path)
    except Resolver404:return ''
    if match.url_name not in ROOTS|DETAILS:return ''
    query=urlencode([(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if k not in ['return_to','saved']])
    return urlunsplit(('','',parsed.path,query,parsed.fragment))

def origin(request):
    for data in [request.POST,request.GET]:
        if 'return_to' in data:return safe_return(request,data.get('return_to'))
    ref=request.META.get('HTTP_REFERER','')
    if not ref:return ''
    parsed=urlsplit(ref)
    if not url_has_allowed_host_and_scheme(ref,{request.get_host()},require_https=request.is_secure()):return ''
    nested=dict(parse_qsl(parsed.query)).get('return_to')
    return safe_return(request,nested) if nested else safe_return(request,ref)

def label(url):
    name=resolve(urlsplit(url).path).url_name if url else ''
    return 'Voltar ao grupo' if name in {'group_detail','consolidated_edition'} else 'Continuar na lista' if name in ROOTS else 'Voltar à ficha'

def context(request):
    match=getattr(request,'resolver_match',None);name=match.url_name if match else ''
    target=origin(request)
    # A list becomes the origin for its own detail/edit links. An explicit
    # group origin survives intervening profile and evaluation pages.
    if name in ROOTS and not request.GET.get('return_to') and request.method=='GET':target=safe_return(request,request.get_full_path())
    return {'origin':target,'label':label(target) if target else '', 'editing':name in EDITS}

def saved_response(request,fallback,profile='',default_target=''):
    target=origin(request) or safe_return(request,default_target)
    if not target:
        messages.success(request,'Guardado com sucesso.')
        return redirect(fallback)
    if request.POST.get('after_save')=='return':
        messages.success(request,'Guardado com sucesso. Pode continuar nesta lista ou grupo.')
        return redirect(target)
    edit=request.path+'?'+urlencode({'return_to':target})
    token=signing.dumps(dict(user=request.user.pk,target=target,edit=edit,profile=profile or fallback),salt=SALT,compress=True)
    return redirect(reverse('edit_saved')+'?'+urlencode({'token':token}))

@login_required
@require_GET
def saved(request):
    try:
        data=signing.loads(request.GET.get('token',''),salt=SALT,max_age=3600)
        if data['user']!=request.user.pk or not safe_return(request,data['target']):raise signing.BadSignature
    except (signing.BadSignature,KeyError,TypeError,ValueError):return HttpResponseBadRequest('Confirmação inválida ou expirada. Volte à lista de origem.')
    return render(request,'edit_saved.html',{'saved_navigation':data,'return_label':label(data['target'])})

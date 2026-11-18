"""Read-only display controls; calculation and export functions are unchanged."""
from hashlib import sha256
from django.core.paginator import Paginator
from .professional_ui import normalise

SORTS={'name':('direction',False),'-name':('direction',True),'participants':('participated',False),'-participants':('participated',True),'rate':('rate',False),'-rate':('rate',True),'uncovered':('not_participated',True)}

def context(request,data):
    base=request.GET.copy()
    for k in ['export','view','detail_direction','detail_state','page']:base.pop(k,None)
    def url(**changes):
        q=base.copy()
        for k,v in changes.items():
            if v is None:q.pop(k,None)
            else:q[k]=v
        return request.path+'?'+q.urlencode()
    sort=request.GET.get('table_sort',{'highest':'-rate','lowest':'rate','uncovered':'uncovered'}.get(request.GET.get('order'),'name'))
    if sort not in SORTS:sort='name'
    search=request.GET.get('direction_q','').strip()[:200]
    rows=[dict(r) for r in (data or {}).get('directions',[]) if normalise(search) in normalise(r['direction'])]
    key,reverse=SORTS[sort];rows.sort(key=lambda r:normalise(r['direction']));rows.sort(key=lambda r:normalise(r[key]) if key=='direction' else (r[key] if r[key] is not None else -1),reverse=reverse)
    size=request.GET.get('direction_size','10');size=int(size) if size in ['10','20','50'] else 10
    directions=Paginator(rows,size).get_page(request.GET.get('direction_page'))
    for row in directions:
        row['tone']=int(sha256(row['direction'].encode()).hexdigest()[:8],16)%5
        row['detail_url']=url(view='nominal',detail_direction=row['direction'])
        for state in ['participated','absent','completed']:row[state+'_url']=url(view='nominal',detail_direction=row['direction'],detail_state=state)
    headers=[]
    for label,k in [('Direcção','name'),('Total de técnicos',None),('Participaram','participants'),('Sem participação',None),('Concluíram',None),('Taxa','rate')]:
        active=k is not None and sort.lstrip('-')==k
        headers.append({'label':label,'url':url(table_sort=('-'+k if sort==k else k),direction_page=None) if k else None,'aria_sort':('descending' if sort.startswith('-') else 'ascending') if active else 'none','active':active})
    selected=request.GET.get('detail_direction','')[:300];state=request.GET.get('detail_state','');nominal=list((data or {}).get('nominal',[]))
    if selected:nominal=[r for r in nominal if r['direction']==selected or selected=='Direcção por confirmar' and r['direction']=='Por confirmar']
    if state=='participated':nominal=[r for r in nominal if r['effective']]
    elif state=='absent':nominal=[r for r in nominal if not r['effective']]
    elif state=='completed':nominal=[r for r in nominal if r['status']=='Concluído']
    page=Paginator(nominal,20).get_page(request.GET.get('page'))
    detail={'view':'nominal','detail_direction':selected or None,'detail_state':state or None}
    pages=[{'number':i,'url':url(direction_page=i) if isinstance(i,int) else None,'current':i==directions.number} for i in directions.paginator.get_elided_page_range(directions.number,on_each_side=1,on_ends=1)]
    return {'direction_page':directions,'direction_headers':headers,'direction_pages':pages,'direction_q':search,'table_sort':sort,'direction_size':size,'table_query':base.urlencode(),'nominal_url':url(view='nominal'),'participants_url':url(view='nominal',detail_state='participated'),'absent_url':url(view='nominal',detail_state='absent'),'no_training_url':url(view='nominal',state='no_training'),'page':page,'detail_title':selected or 'Lista nominal','detail_state_label':{'participated':'Participaram','absent':'Sem participação','completed':'Concluíram'}.get(state,'Todos os técnicos'),'nominal_previous':url(**detail,page=page.previous_page_number()) if page.has_previous() else None,'nominal_next':url(**detail,page=page.next_page_number()) if page.has_next() else None,'open_nominal':request.GET.get('view')=='nominal','excel_url':url(export='xlsx'),'pdf_url':url(export='pdf')}

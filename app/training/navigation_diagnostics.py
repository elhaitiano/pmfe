"""Opt-in, staff-only timings; SQL parameter values and source records are not recorded."""
import json,time,logging
from collections import Counter
from django.conf import settings
from django.db import connection


class NavigationDiagnostics:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if request.headers.get('X-PMF-Diagnostic')!='navigation' or not request.user.is_authenticated or not request.user.is_staff:
            return self.get_response(request)
        count=0;sql_seconds=0;queries=Counter()
        def measure(execute,sql,params,many,context):
            nonlocal count,sql_seconds
            started=time.perf_counter()
            try:return execute(sql,params,many,context)
            finally:
                count+=1;sql_seconds+=time.perf_counter()-started
                queries[' '.join(sql.split())]+=1
        started=time.perf_counter()
        with connection.execute_wrapper(measure):response=self.get_response(request)
        duration=(time.perf_counter()-started)*1000
        response['Server-Timing']=f'pmf;dur={duration:.2f}, sql;dur={sql_seconds*1000:.2f};desc="{count} queries"'
        response['X-PMF-SQL-Count']=str(count)
        record={'path':request.path,'server_ms':round(duration,2),'sql_ms':round(sql_seconds*1000,2),'sql_count':count,'repeated_sql':queries.most_common(12)}
        target=settings.BASE_DIR.parent.parent/'entregaveis/desempenho-navegacao-20261105/server-profiles.jsonl'
        try:
            with target.open('a',encoding='utf8') as f:f.write(json.dumps(record)+'\n')
        except OSError:
            logging.getLogger('django.server').warning('PMF-PERF could not write the local profile')
        logging.getLogger('django.server').info('PMF-PERF %s %.0f ms; SQL %s / %.0f ms',request.path,duration,count,sql_seconds*1000)
        return response

"""Human descriptions for the existing administrative audit page; no writes."""
import json

TITLES={'PLANNING_DRAFT_SAVE':'Preparação guardada','DRAFT_PARTICIPANTS':'Formandos do grupo actualizados',
        'PLANNING_DRAFT_PUBLISH':'Grupo publicado','PLANNING_GROUP_CORRECTION':'Dados do grupo corrigidos',
        'ELIGIBILITY_AUTHORIZATION':'Participação analisada e autorizada'}

def present(event):
    event.display_title=TITLES.get(event.action,event.action.replace('_',' ').capitalize())
    try:data=json.loads(event.detail)
    except (ValueError,TypeError):
        event.display_summary=event.detail;event.display_rows=[];return event
    if not isinstance(data,dict):data={}
    event.display_summary=data.get('reason') or 'Operação registada na auditoria.'
    event.display_rows=[]
    for key,label in [('draft','Preparação'),('group','Grupo'),('revision','Revisão'),('reference','Referência documental')]:
        if data.get(key) is not None:event.display_rows.append((label,str(data[key])))
    if isinstance(data.get('before'),list) and isinstance(data.get('after'),list):
        event.display_rows.extend([('Formandos antes',str(len(data['before']))),('Formandos depois',str(len(data['after'])))])
    return event

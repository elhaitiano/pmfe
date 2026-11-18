"""A read-only summary of the existing collective-validation engine.

No new approval, token, persistence or document-to-person matching is introduced.
"""
from collections import Counter
from django.core.exceptions import ValidationError
from . import group_validation as service
from .models import (Course,ConsolidatedParticipation,HistoricalInterfaceLink,
                     Training,TrainingDocumentState,TrainingEvaluation)
from django.urls import reverse

LABELS={'A':'A — Pronto para validação','B':'B — Validação parcial','C':'C — Necessita de revisão'}

def summarize(data):
    rows=data['rows'];pending=data['pending']
    eligible=sum(bool(r['eligible']) for r in rows)
    protected=sum(r['existing'] is not None for r in rows)
    excluded=[r for r in rows if not r['eligible'] and r['existing'] is None]
    reviews=[r for r in rows if r['existing'] is not None and r['inconsistent']]
    inconsistent=any(r['inconsistent'] for r in rows)
    # Protected decisions are displayed separately, never rewritten or counted as pending.
    category='A' if eligible and not excluded and not pending and not inconsistent else 'B' if eligible else 'C'
    reasons=Counter(reason for row in excluded for reason in row['reasons'])
    reasons.update(p['reason'] for p in pending)
    reasons.update(reason for row in reviews for reason in row['reasons'])
    if inconsistent and not reasons:reasons['Evidência alterada após decisão anterior; revisão individual.']+=1
    if not rows and not pending:reasons['Grupo sem participações operacionais associadas.']+=1
    if protected and not eligible and not excluded and not pending:reasons['Decisões anteriores preservadas; sem novas participações elegíveis.']+=protected
    if inconsistent and category=='A':category='B'
    return {'category':category,'label':LABELS[category],'eligible':eligible,'pending':len(excluded)+len(pending),'protected':protected,'review_existing':len(reviews),'participants':data['total_enrolled'],'participations':len(rows),'completed':data['completed'],'inconsistent':inconsistent,'reasons':list(reasons.items()),'pending_rows':excluded+pending+reviews,'academic_approval_generated':False,'basis':'Regras actuais da validação colectiva; pré-visualização e confirmação autorizada continuam obrigatórias.'}

def edition_data(groups):
    """Batch-load only existing FK associations; apply the SAME assess function.

    New rows need no signing fingerprint for a read-only summary. Existing
    supplements retain their exact fingerprint check. No token is returned.
    """
    groups=list(groups);group_ids=[g.pk for g in groups]
    parts=list(ConsolidatedParticipation.objects.filter(edition_id__in=group_ids,active=True).select_related('edition','employee').order_by('pk'))
    links=list(HistoricalInterfaceLink.objects.filter(participation_id__in=[p.pk for p in parts]).order_by('pk'))
    ids=[link.training_id for link in links]
    trainings={t.pk:t for t in Training.objects.filter(pk__in=ids).select_related('employee')}
    documents={d.training_id:d for d in TrainingDocumentState.objects.filter(training_id__in=ids)}
    evaluations={e.training_id:e for e in TrainingEvaluation.objects.filter(training_id__in=ids)}
    protected=service.prior_validations(ids)
    courses={c.pk:c for c in Course.objects.filter(pk__in={g.course_id for g in groups})}
    by_part={link.participation_id:trainings.get(link.training_id) for link in links}
    result={}
    for group in groups:
        key='edition:'+str(group.pk);rows=[];pending=[];course=courses[group.course_id]
        members=[p for p in parts if p.edition_id==group.pk]
        if len(members)>service.MAX_ROWS:result[key]=None;continue
        for p in members:
            t=by_part.get(p.pk)
            if t is None:
                pending.append({'name':p.employee.name,'employee_id':p.employee_id,'reason':'Participação sem ligação operacional confirmada.','correction_url':reverse('consolidated_participation',args=[p.pk])});continue
            d=documents.get(t.pk);evaluation=evaluations.get(t.pk)
            fingerprint=service.dependency_fingerprint(t,p,d,group,course,evaluation,[]) if service.MARKER in (t.source_data or {}) else ''
            row=service.assess(t,p,d,group,course,key,fingerprint,[],evaluation,protected.get(t.pk),include_source_data=False)
            row['participation_url']=reverse('consolidated_participation',args=[p.pk]);rows.append(row)
        rows.sort(key=lambda r:r['training_id'])
        result[key]={'key':key,'rows':rows,'pending':pending,'total_enrolled':len({r['employee_id'] for r in rows}|{r['employee_id'] for r in pending}),'completed':sum(r['completion_proved'] for r in rows)}
    return result

def attach(groups,user):
    service.authorize(user)
    groups=list(groups)
    batched=edition_data([g for g in groups if getattr(g,'historical_edition',False)])
    for group in groups:
        key=('edition:' if getattr(group,'historical_edition',False) else 'group:')+str(group.pk)
        try:
            data=batched[key] if key in batched else service.collect(key)
            if data is None:raise ValidationError('Limite de registos.')
            summary=summarize(data)
        except ValidationError:
            summary={'category':'C','label':LABELS['C'],'eligible':0,'pending':None,'protected':0,'reasons':[('Grupo excede os limites de revisão; consultar a pré-visualização existente.',1)],'pending_rows':[],'academic_approval_generated':False}
        summary['key']=key;summary['preview_url']=''
        summary['preview_url']=reverse('group_validation')+'?group='+key
        can_correct=user.is_staff and user.has_perm('training.change_training')
        for row in summary['pending_rows']:
            row['documentary_url']=row['correction_url'] if can_correct else row.get('participation_url',summary['preview_url'])
        group.documentary_readiness=summary
    return groups

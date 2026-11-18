from .professional_ui import person_ids
"""One reconciled historical projection shared by pages, indicators and exports."""
import hashlib,json,os
from collections import Counter,defaultdict
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path
from django.db import transaction,DatabaseError
from django.core.exceptions import ValidationError
from .models import (ConsolidationRun,OfficialRosterEntry,ConsolidatedEdition,ConsolidatedParticipation,
    Employee,Course,Training,HistoricalDocumentaryParticipation,AuditEvent)
from .documentary_import import digest
from .certificate_evidence import io_path,normalize


def enabled():
    try:return ConsolidationRun.objects.filter(active=True).exists()
    except DatabaseError:return False


def workers(rows=None,include_legacy=False):
    rows=Employee.objects.all() if rows is None else rows
    return rows if include_legacy or not enabled() else rows.filter(official_roster_entry__current=True)


def participations():
    return ConsolidatedParticipation.objects.filter(active=True,employee__official_roster_entry__current=True).select_related('employee','edition','edition__course')

def summary_participations():
    """Keep source evidence accessible, without decoding it for every summary row."""
    return participations().defer('evidence', 'employee__source_data',
                                 'edition__evidence', 'edition__course__source_metadata')


def stats(rows):
    rows=list(rows);editions={p.edition_id:p.edition for p in rows};known=[p for p in rows if p.attendance_percent is not None]
    from .certificate_register import certificate_statistics
    from .query_helpers import dashboard_summary
    projected=dashboard_summary(Training.objects.filter(interface_link__participation_id__in=[p.pk for p in rows]).only('pk','employee_id','certified','certificate_issued','certificate_delivery_date','status','end'))
    certificates=certificate_statistics(projected)
    expected=sum((p.present_count or 0)+(p.absent_count or 0) for p in known)
    grade_groups=defaultdict(list)
    for p in rows:
        for grade in p.grades:grade_groups[(grade['stage'],grade['scale'],grade.get('phase',''))].append(Decimal(grade['value']))
    return {**certificates,'participations':len(rows),'people':len({p.employee_id for p in rows}),'actions':len({p.edition.course_reference for p in rows}),
        'editions':len(editions),'groups':sum(bool(e.group_label) for e in editions.values()),
        'with_grades':sum(bool(p.grades) for p in rows),'with_final_grade':sum(any(g['stage']=='final' for g in p.grades) for p in rows),
        'people_with_attendance':len({p.employee_id for p in rows if p.present_count is not None and p.present_count>0}),
        'registrations_with_attendance':sum(p.present_count is not None and p.present_count>0 for p in rows),
        'nominal_only':sum(p.present_count is None and not p.grades and p.completed is None for p in rows),
        'with_duration':sum(p.edition.declared_days is not None for p in rows),'with_total_hours':sum(p.edition.total_hours is not None for p in rows),
        'with_attendance':len(known),'attendance':sum(p.present_count for p in known)*Decimal(100)/expected if expected else None,
        'planned_hours':sum((p.edition.total_hours for p in rows if p.edition.total_hours is not None),Decimal(0)),
        'attended_hours':sum((p.attended_hours for p in rows if p.attended_hours is not None),Decimal(0)),
        'with_attended_hours':sum(p.attended_hours is not None for p in rows),'with_result':sum(bool(p.result) for p in rows),
        'completed':sum(p.completed is True for p in rows),
        'grade_averages':[{'stage':k[0],'scale':k[1],'phase':k[2],'count':len(v),'average':sum(v)/len(v)} for k,v in sorted(grade_groups.items())]}


def filtered(rows,params):
    q=params.get('q','').strip()
    if q:
        from django.db.models import Q
        rows=rows.filter(Q(employee_id__in=person_ids(q))|Q(edition__title__icontains=q))
    if params.get('action'):rows=rows.filter(edition__course__source_action_id=params['action'])
    for name,field in [('employee','employee_id'),('course','edition__course_reference'),('group','edition_id'),('direction','employee__direction')]:
        value=params.get(name,'').strip()
        if value:
            if name in ('employee','group') and not value.isdecimal():return rows.none()
            rows=rows.filter(**{field:value})
    year=params.get('year','')
    if year.isdecimal() and len(year)==4:rows=rows.filter(edition__start__year=int(year))
    for name,field in [('start','edition__end__gte'),('end','edition__start__lte')]:
        value=params.get(name,'')
        if value:
            try:value=date.fromisoformat(value)
            except ValueError:continue
            rows=rows.filter(**{field:value})
    return rows


def parse_official_date(value):
    if not value:return None
    for fmt in ['%d-%m-%Y','%d/%m/%Y','%Y-%m-%d','%Y-%m-%d %H:%M:%S']:
        try:return datetime.strptime(str(value),fmt).date()
        except ValueError:pass
    return None


@transaction.atomic
def apply_plan(folder,source_root):
    folder=Path(folder);source_root=Path(source_root)
    def read(name):return json.loads((folder/(name+'.json')).read_text(encoding='utf-8'))
    roster=read('CADASTRO_OFICIAL');editions=read('EDICOES_CONSOLIDADAS');parts=read('PARTICIPACOES_CONSOLIDADAS')
    key='EPAL_CONSOLIDATION_'+digest([roster,editions,parts])
    previous=ConsolidationRun.objects.filter(key=key).first()
    if previous:return previous.report
    master=roster['source'];master_bytes=Path(master['path']).read_bytes()
    if hashlib.sha256(master_bytes).hexdigest()!=master['sha256']:raise ValidationError('O cadastro oficial mudou desde a conferência.')
    for sha,relative in read('FONTES_VERIFICADAS').items():
        path=folder.parent/'conferencia-integral-20261104/snapshot-mensal-atual.xlsx' if relative=='SNAPSHOT_MENSAL_ATUAL/snapshot-mensal-atual.xlsx' else io_path(source_root/relative)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValidationError('Uma fonte mudou desde a conferência: '+relative)
    actor='Consolidação documental EPAL';changes=Counter();people={};audit=[]
    for w in roster['workers']:
        person,created=Employee.objects.get_or_create(number=w['number'],defaults={'name':w['name']})
        before={f:getattr(person,f) for f in ['name','phone','direction','category','function','area','birth_date','admission_date']}
        for field in ['name','phone','direction','category','function','area']:
            if w.get(field):setattr(person,field,w[field])
        for field,literal in [('birth_date','birth_literal'),('admission_date','admission_literal')]:
            value=parse_official_date(w.get(literal))
            if value:setattr(person,field,value)
        person.source_data={**person.source_data,'EPAL_OFFICIAL_ROSTER':{'sha256':master['sha256'],'source_row':w['source_row'],'verified':True},
            'Validação Cadastro EPAL':'Definitivo — fonte oficial conferida'}
        person.save()
        OfficialRosterEntry.objects.update_or_create(employee=person,defaults={'current':True,'source_sha256':master['sha256'],'source_row':w['source_row'],'evidence':w})
        people[w['number']]=person
        after={f:getattr(person,f) for f in before}
        if created:changes['employees_created']+=1
        elif before!=after:changes['employees_corrected']+=1
        if created or before!=after:audit.append(AuditEvent(actor=actor,action='OFFICIAL_ROSTER_RECONCILED',detail=json.dumps({'number':w['number'],'before':before,'after':after,'source_sha256':master['sha256'],'source_row':w['source_row']},ensure_ascii=False,default=str)))
    OfficialRosterEntry.objects.exclude(employee__number__in=people).update(current=False)
    mapped={};titles={normalize(c.title):c for c in Course.objects.all()}
    for e in editions:
        course=Course.objects.filter(pk=e.get('course_id')).first() if e.get('course_id') else titles.get(normalize(e['title']))
        if course is None:
            course,created=Course.objects.get_or_create(source_system='epal-historical-evidence',source_action_id=digest(e['code']),defaults={
                'title':e['title'],'validation_status':'pending','source_metadata':{'course_reference':e['code'],'source_evidence':True},'validation_notes':'Designação comprovada no histórico; não inventa duração padrão ou código interno.'})
            if created:changes['courses_added_from_evidence']+=1
            titles[normalize(course.title)]=course
        def unique(field):return e[field][0] if len(e[field])==1 else None
        values={'course':course,'course_reference':e['code'],'title':e['title'],'group_label':e['group'],'start':e['start'],'end':e['end'],
            'location':unique('locations') or '','trainer':unique('trainers') or '','provider':unique('providers') or '',
            'declared_days':unique('days'),'total_hours':unique('total_hours'),'hours_per_day':unique('hours_per_day'),
            'expected_dates':e['expected_dates'],'evidence':e,'active':True}
        item,created=ConsolidatedEdition.objects.update_or_create(source_key=e['key'],defaults=values)
        mapped[e['key']]=item
        if created:changes['editions_created']+=1
        import re
        match=re.fullmatch(r'G?([0-9]+)',e['group'],re.I)
        if match and course.next_group<=int(match.group(1)):
            course.next_group=int(match.group(1))+1;course.save(update_fields=['next_group'])
    legacy_employees=dict(Training.objects.values_list('pk','employee_id'));doc_employees=dict(HistoricalDocumentaryParticipation.objects.values_list('pk','employee_id'))
    for p in parts:
        person=people[p['number']]
        if any(legacy_employees.get(pk)!=person.pk for pk in p['legacy_training_ids']):raise ValidationError('Associação de histórico a mecanográfico incorreto.')
        if any(doc_employees.get(pk)!=person.pk for pk in p['documentary_participation_ids']):raise ValidationError('Participação documental de outro formando.')
        for grade in p['grades']:
            value,scale=Decimal(grade['value']),Decimal(grade['scale'])
            if not value.is_finite() or not scale.is_finite() or scale<=0 or not 0<=value<=scale:raise ValidationError('Nota fora da escala original.')
        values={'employee':person,'edition':mapped[p['edition_key']],'present_count':p['present_count'],'absent_count':p['absent_count'],
            'attendance_complete':p['attendance_complete'],'attendance_percent':p['attendance_percent'],'attended_hours':p['attended_hours'],
            'grades':p['grades'],'result':p['result'],'completed':p['completed_value'],'legacy_training_ids':p['legacy_training_ids'],
            'documentary_participation_ids':p['documentary_participation_ids'],'evidence':p,'active':True}
        existing=ConsolidatedParticipation.objects.filter(source_key=p['key']).first()
        if existing:
            for field in ['present_count','absent_count','attendance_percent','attended_hours','result','completed']:
                if values[field] is None or values[field]=='':values[field]=getattr(existing,field)
            if not values['grades']:values['grades']=existing.grades
        item,created=ConsolidatedParticipation.objects.update_or_create(source_key=p['key'],defaults=values)
        if created:changes['participations_created']+=1
    ConsolidatedParticipation.objects.filter(active=True).exclude(source_key__in=[p['key'] for p in parts]).update(active=False)
    ConsolidatedEdition.objects.filter(active=True).exclude(source_key__in=mapped).update(active=False)
    official_ids=[p.pk for p in people.values()]
    extras=list(Employee.objects.exclude(pk__in=official_ids).values('pk','number','name'))
    for e in extras:e['historical_rows']=Training.objects.filter(employee_id=e['pk']).count()
    report={**read('RESUMO_CONSOLIDACAO'),'official_source_total':len(people),'official_integrated_total':OfficialRosterEntry.objects.filter(current=True).count(),
        'legacy_roster_entries_preserved':extras,'changes':dict(changes),'source_files':read('FONTES_VERIFICADAS'),
        'source_issues':read('FONTES_ALTERADAS_OU_INDISPONIVEIS'),'association_decisions':read('CORRECOES_ASSOCIACAO'),'plan_directory':str(folder.resolve()),
        'unassigned_source_rows':read('ASSOCIACOES_SEM_EDICAO_UNICA'),'master':master,'certificates_untouched':True,
        'source_notes':'Notas, menções calculadas e resultados conservam fórmulas e escalas; nenhuma nota resulta de presenças.',
        'reconciliation':read('CONFERENCIA_FINAL')}
    ConsolidationRun.objects.filter(active=True).update(active=False)
    ConsolidationRun.objects.create(key=key,active=True,master_sha256=master['sha256'],report=report)
    audit.append(AuditEvent(actor=actor,action='HISTORICAL_CONSOLIDATION_APPLIED',detail=json.dumps({'key':key,'changes':dict(changes),'official_total':len(people),'certificates_untouched':True},ensure_ascii=False)))
    AuditEvent.objects.bulk_create(audit)
    if report['official_source_total']!=report['official_integrated_total']:raise ValidationError('Cadastro não reconciliado com a fonte.')
    return report

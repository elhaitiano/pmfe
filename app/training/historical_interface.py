"""Use proven consolidated facts in the original application's actual workflows."""
import json
from decimal import Decimal
from django.db import transaction
from django.forms.models import model_to_dict
from .models import (Employee,Training,OfficialRosterEntry,ConsolidatedParticipation,HistoricalInterfaceLink,
    OriginalHistorySnapshot,TrainingDocumentState,HistoricalDocumentaryParticipation,Course,PlanningLock,TrainingCentre,Trainer)


@transaction.atomic
def install(participation_ids=None):
    links=[];created=updated=0
    employee_ids=set(OfficialRosterEntry.objects.filter(current=True).values_list('employee_id',flat=True))
    if participation_ids is None:
        Employee.all_objects.exclude(pk__in=employee_ids).update(registry_active=False)
        for entry in OfficialRosterEntry.objects.filter(current=True).select_related('employee'):
            p=entry.employee;w=entry.evidence
            for field,key in [('identity_number','bi'),('social_security_number','inss'),('gender','sex'),('marital_status','civil_status'),('cost_center','area')]:
                if w.get(key):setattr(p,field,w[key])
            p.registry_active=True;p.registration_validation='Cadastro oficial conferido';p.registration_origin='Fonte oficial EPAL';p.source_row=entry.source_row;p.save()
        for t in Training.all_objects.exclude(interface_state='consolidated'):
            snapshot=json.loads(json.dumps({f.attname:getattr(t,f.attname) for f in t._meta.concrete_fields},default=str))
            OriginalHistorySnapshot.objects.get_or_create(original_training_id=t.pk,defaults={'snapshot':snapshot})
    from .historical_review import active_pdfs
    pdf_by_training={};pdf_by_documentary={}
    for pdf in active_pdfs().only('pk','training_id','documentary_participation_id'):
        if pdf.training_id:pdf_by_training.setdefault(pdf.training_id,[]).append(pdf.pk)
        if pdf.documentary_participation_id:pdf_by_documentary.setdefault(pdf.documentary_participation_id,[]).append(pdf.pk)
    used=set()
    participants=ConsolidatedParticipation.objects.filter(active=True)
    if participation_ids is not None:participants=participants.filter(pk__in=participation_ids)
    for p in participants.select_related('employee','edition','edition__course').order_by('pk'):
        if p.employee_id not in employee_ids:continue
        link=HistoricalInterfaceLink.objects.filter(participation=p).first()
        t=Training.all_objects.get(pk=link.training_id) if link else Training.all_objects.filter(pk__in=p.legacy_training_ids,employee_id=p.employee_id).order_by('pk').first()
        if t is not None and t.pk in used:raise ValueError('Duas participações tentam reutilizar o mesmo histórico.')
        e=p.edition
        if t is None:t=Training(employee=p.employee,source_key='epal-consolidated:'+p.source_key)
        prior_delivery=t.certified
        prior_delivery_date=t.certificate_delivery_date
        state_ids=set(p.legacy_training_ids)
        if t.pk:state_ids.add(t.pk)
        old_data=dict(t.source_data or {});old_data.pop('_presentation_grade',None);old_data.pop('_certificate_delivery_declaration',None)
        facts={'participation':p.source_key,'edition':e.source_key,'attended_hours':str(p.attended_hours) if p.attended_hours is not None else None,
            'total_hours':str(e.total_hours) if e.total_hours is not None else None,'attendance_percent':str(p.attendance_percent) if p.attendance_percent is not None else None,
            'absent_count':p.absent_count,'result':p.result,'grades':p.grades,'group':e.group_label,'completed':p.completed}
        facts['archived_pdf_ids']=sorted({pdf for key in state_ids for pdf in pdf_by_training.get(key,[])}|{pdf for key in p.documentary_participation_ids for pdf in pdf_by_documentary.get(key,[])})
        finals=[g for g in p.grades if g['stage']=='final'];mentions=[g for g in p.grades if g['stage']=='certificate_mention']
        grade=finals[0] if len(finals)==1 else mentions[0] if len(mentions)==1 else None
        if grade:
            value,scale=Decimal(grade['value']),Decimal(grade['scale'])
            assessment={'kind':'percentage','normalized_ratio':str(value/scale),'raw':grade['value']+'/'+grade['scale'],'stage':grade['stage'],'provenance':grade.get('claims',[])}
        else:assessment={}
        old_data.update(_EPAL_CONSOLIDATED_FACTS=facts,_original_assessment=assessment)
        t.source_data=old_data;t.interface_state='consolidated';t.title=e.title;t.start=e.start;t.end=e.end;t.course=e.course
        t.area=e.course.area if e.course_id else '';t.trainer=e.trainer;t.location=e.location;t.provider=e.provider
        t.days=e.declared_days;t.hours_per_day=e.hours_per_day;t.attendance=p.present_count;t.attendance_confirmed=p.attendance_complete
        numeric20=[g for g in finals if Decimal(g['scale'])==20] or [g for g in mentions if Decimal(g['scale'])==20]
        t.grade=Decimal(numeric20[0]['value']) if len(numeric20)==1 else None
        t.result='Pendente';t.status='Concluído' if p.completed is True else 'Por confirmar'
        t.source_course_code=e.course_reference if not e.course_reference.startswith('TITLE:') else ''
        states=list(TrainingDocumentState.objects.filter(training_id__in=state_ids))+list(HistoricalDocumentaryParticipation.objects.filter(pk__in=p.documentary_participation_ids))
        if any(s.learner_delivered is True for s in states):t.certified=True
        elif states and all(s.learner_delivered is False for s in states):t.certified=False
        else:t.certified=None
        facts['historical_delivery_confirmed']=t.certified is True
        dates={s.learner_date for s in states if s.learner_delivered is True and s.learner_date}
        t.certificate_delivery_date=next(iter(dates)) if len(dates)==1 else None
        if participation_ids is not None and prior_delivery is True:
            t.certified=True
            facts['historical_delivery_confirmed']=True
            if not t.certificate_delivery_date:t.certificate_delivery_date=prior_delivery_date
        issued=[s.issued for s in states if hasattr(s,'issued') and s.issued is not None]
        if issued and len(set(issued))==1:t.certificate_issued=issued[0]
        hr=[s.hr_delivered for s in states if s.hr_delivered is not None]
        if hr and len(set(hr))==1:t.certificate_hr_delivered=hr[0]
        certificates=old_data.get('_validated_certificates') or []
        if certificates:
            # A later projection must retain the responsible's evidence-scoped
            # confirmation and the final certificate's calendar.
            periods={(c['start'],c['end']) for c in certificates}
            if len(periods)==1:
                from datetime import date
                start,end=next(iter(periods));t.start=date.fromisoformat(start);t.end=date.fromisoformat(end)
            t.certified=True;t.certificate_issued=True;t.status='Concluído'
            facts['historical_delivery_confirmed']=True
            if not t.certificate_delivery_date:t.certificate_delivery_date=prior_delivery_date
        from .interface_sync import inputs
        if participation_ids is not None and t.pk:t.version+=1
        facts['inputs']=inputs(t);facts['sync_version']=t.version
        was_new=t.pk is None;t.save();used.add(t.pk)
        HistoricalInterfaceLink.objects.update_or_create(participation=p,defaults={'training':t})
        created+=was_new;updated+=not was_new
    if participation_ids is not None:
        return {'new_interface_rows':created,'existing_rows_linked':updated}
    Training.all_objects.exclude(pk__in=used).update(interface_state='legacy_preserved')
    for c in Course.objects.all():
        if not c.code and c.internal_code and not Course.objects.exclude(pk=c.pk).filter(code__iexact=c.internal_code).exists():c.code=c.internal_code
        c.validation_status={'pending':'Por confirmar','validated':'Validado','review':'Rever','confirmed':'Validado'}.get(c.validation_status,c.validation_status)
        c.save()
    PlanningLock.objects.get_or_create(pk=1,defaults={'revision':0})
    from .scheduling_guard import reservation_transaction
    with reservation_transaction():
        for code,name in [('EPAL_KIKUXI','Centro de Formação da EPAL, Kikuxi'),('KIFANGONDO','ETA Kifangondo'),('KILAMBA','ETA Kilamba'),('MARCAL','CD Marçal')]:
            TrainingCentre.objects.get_or_create(code=code,defaults={'name':name,'active':True})
    for name in set(Training.objects.exclude(trainer='').values_list('trainer',flat=True)):
        if name.strip().casefold() in ['por definir','por confirmar','formador','formador por definir','a definir','não indicado','n/d']:continue
        trainer,_=Trainer.objects.get_or_create(name=name)
        Training.objects.filter(trainer=name).update(trainer_record=trainer)
    from pathlib import Path
    from .models import AssistantGuide
    guides=json.loads((Path(__file__).parent/'resources/original_guides.json').read_text(encoding='utf-8-sig'))
    for guide in guides:
        values=dict(guide);slug=values.pop('slug')
        for field in ['keywords','steps']:
            if isinstance(values[field],str):values[field]=json.loads(values[field])
        AssistantGuide.objects.update_or_create(slug=slug,defaults=values)
    from django.urls import reverse,NoReverseMatch
    for guide in AssistantGuide.objects.filter(enabled=True):
        try:reverse(guide.route)
        except NoReverseMatch:
            if guide.route not in ['employee_detail','employee_data','training_create','group_detail']:guide.enabled=False;guide.save(update_fields=['enabled'])
    return {'official_employees':Employee.objects.count(),'participations':Training.objects.count(),'originals_preserved':OriginalHistorySnapshot.objects.count(),
        'new_interface_rows':created,'existing_rows_linked':updated,'historical_links':HistoricalInterfaceLink.objects.count()}

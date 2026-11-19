from .professional_ui import person_ids
"""Catalogue, numbered groups and reviewed enrolments. No attendance is inferred."""
import hashlib, json, unicodedata
from datetime import date, timedelta
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError, OperationalError
from django.db.models import F, Q, Count
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponseBadRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from .forms import StyledModelForm
from .models import Course, TrainingGroup, GroupSession, Enrollment, Employee, Training
from .views import staff_required, record
from .widgets import numeric_text_widgets, date_widgets
from .models import AuditEvent, Trainer, TrainingCentre, TrainingRoom
from .scheduling import save_group, detect_conflicts, conflict_message, validate_reservation
from .scheduling_guard import reservation_transaction
from .group_roster import summaries as roster_summaries, group_trainings, attendance_rows, source_keys


def check_submitted_total(form, total):
    if 'total_hours' not in form.data:
        return
    try:
        submitted = Decimal(form.data.get('total_hours', ''))
        if not submitted.is_finite() or total is None or submitted != total:
            raise ValueError
    except (ValueError, ArithmeticError):
        form.add_error(None, 'O total enviado diverge de dias × horas por dia.')


def normalise(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


class CourseForm(StyledModelForm):
    duration_reason=forms.CharField(label='Motivo da correcção de duração',required=False,max_length=4000)
    duration_reference=forms.CharField(label='Referência documental da duração',required=False,max_length=1000)
    revision = forms.IntegerField(widget=forms.HiddenInput, required=False)
    class Meta:
        model = Course
        fields = ['code','title','area','contents','default_days','default_hours','validation_status','validation_notes','internal_code','active']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.duration_before=(self.instance.default_days,self.instance.default_hours)
        self.fields['default_days'].widget.attrs['id']='planDayCount'
        self.fields['area'].required = True
        self.fields['validation_status'].required = False
        if self.instance.pk and self.instance.code:
            self.fields['code'].disabled = True

    def clean_validation_status(self):
        return self.cleaned_data.get('validation_status') or self.instance.validation_status or 'Por confirmar'

    def clean_code(self):
        value = self.cleaned_data['code'].strip().upper()
        if value and Course.objects.exclude(pk=self.instance.pk).filter(code__iexact=value).exists():
            raise ValidationError('Este código já pertence a outra acção de formação.')
        return value

    def clean_title(self):
        value = self.cleaned_data['title'].strip()
        if any(normalise(value) in {normalise(c.title),*(normalise(a) for a in c.aliases)} for c in Course.objects.exclude(pk=self.instance.pk)):
            raise ValidationError('Esta acção já existe no catálogo. Abra-a para criar outro grupo.')
        return value

    @property
    def total_hours(self):
        data=getattr(self,'cleaned_data',{}) if self.is_bound else {'default_days':self.instance.default_days,'default_hours':self.instance.default_hours}
        days,hours=data.get('default_days'),data.get('default_hours')
        return Decimal(days)*hours if days is not None and hours is not None else None
    def clean(self):
        data=super().clean()
        check_submitted_total(self,self.total_hours)
        after=(data.get('default_days'),data.get('default_hours'))
        if self.instance.pk and after!=self.duration_before:
            for field in ('duration_reason','duration_reference'):
                if not data.get(field,'').strip():self.add_error(field,'Justificação e documento são obrigatórios para corrigir a duração.')
        return data


DEFAULT_LOCATIONS = [
    'Centro de Formação da EPAL — Kikuxi',
    'ETA Kifangondo — Icolo e Bengo',
    'ETA Kilamba — Luanda',
    'CD Marçal — Luanda',
]


class GroupForm(forms.Form):
    dates = forms.CharField(required=False, widget=forms.HiddenInput(attrs={'id':'legacy-session-dates'}))
    correction_reason = forms.CharField(label='Motivo da correcção',max_length=1000,required=False)
    session_dates = forms.CharField(label='Dias efectivos de formação', required=False, widget=forms.Textarea(attrs={'rows':5}), help_text='Seleccione no calendário ou indique datas AAAA-MM-DD, uma por linha. Nenhum dia é seleccionado automaticamente.')
    centre = forms.ModelChoiceField(label='Local', queryset=TrainingCentre.objects.all(), required=False, widget=forms.HiddenInput)
    room = forms.ModelChoiceField(label='Sala', queryset=TrainingRoom.objects.select_related('centre').all(), required=False, widget=forms.HiddenInput)
    planning_confirmation = forms.ChoiceField(required=False, label='Confirmação do planeamento', choices=[('Provisorio','Guardar provisório'),('Confirmado','Confirmar reserva'),('Por rever','Por rever')], initial='Provisorio')
    start = forms.DateField(label='Data de início', input_formats=['%d/%m/%Y', '%Y-%m-%d'], widget=forms.DateInput(format='%d/%m/%Y', attrs={'placeholder': 'dd/mm/aaaa', 'inputmode': 'numeric', 'maxlength': '10', 'pattern': '[0-9]{2}/[0-9]{2}/[0-9]{4}'}))
    end = forms.DateField(label='Data de fim', input_formats=['%d/%m/%Y', '%Y-%m-%d'], widget=forms.DateInput(format='%d/%m/%Y', attrs={'placeholder': 'dd/mm/aaaa', 'inputmode': 'numeric', 'maxlength': '10', 'pattern': '[0-9]{2}/[0-9]{2}/[0-9]{4}'}))
    trainer = forms.ModelChoiceField(label='Formador(a)', queryset=Trainer.objects.none(), empty_label='Seleccione o formador')
    provider = forms.CharField(label='Entidade formadora',max_length=200,required=False)
    location = forms.CharField(label='Local da formação', max_length=200)
    new_location = forms.CharField(label='Nome e localização do novo local', max_length=200, required=False)
    capacity = forms.IntegerField(label='Capacidade',min_value=1,max_value=1000,initial=30)
    hours_per_day = forms.DecimalField(label='Horas de formação por dia',min_value=Decimal('.5'),max_value=24,max_digits=4,decimal_places=2,initial=7)
    break_hours = forms.DecimalField(label='Pausas por dia (horas)',min_value=0,max_value=12,max_digits=4,decimal_places=2,initial=0)
    start_time = forms.TimeField(label='Hora de início',widget=forms.TimeInput(attrs={'type':'time'}))
    end_time = forms.TimeField(label='Hora de fim',widget=forms.TimeInput(attrs={'type':'time'}))
    revision = forms.IntegerField(widget=forms.HiddenInput,required=False)
    def __init__(self,*a,**kw):
        current_trainer = kw.pop('current_trainer', None)
        if a and a[0] is not None:
            payload=a[0].copy()
            raw=payload.get('dates','')
            if isinstance(raw,(list,tuple)):
                raw='\n'.join(v.isoformat() if hasattr(v,'isoformat') else str(v) for v in raw)
                payload['dates']=raw
            if raw and not payload.get('session_dates'):
                payload['session_dates']=raw
                try:
                    parsed=sorted(date.fromisoformat(v.strip()) for v in str(raw).replace(',','\n').splitlines() if v.strip())
                    if parsed:
                        if not payload.get('start'):payload['start']=parsed[0].isoformat()
                        if not payload.get('end'):payload['end']=parsed[-1].isoformat()
                except ValueError:pass
            a=(payload,*a[1:])
        super().__init__(*a,**kw)
        self.fields['trainer'].queryset = Trainer.objects.filter(Q(active=True) | Q(pk=current_trainer)).order_by('name', 'pk')
        from .eligibility import enabled
        if enabled() and self.is_bound and self.data.get('trainer') and not str(self.data['trainer']).isdecimal():
            self.fields['trainer']=forms.CharField(label='Formador(a)',max_length=200,required=False)
        numeric_text_widgets(self.fields)
        date_widgets(self.fields)
        names = list(DEFAULT_LOCATIONS)
        names.extend(TrainingCentre.objects.filter(active=True).values_list('name', flat=True))
        names.extend(TrainingGroup.objects.exclude(location='').values_list('location', flat=True).distinct())
        if self.initial.get('location'):
            names.append(self.initial['location'])
        names = list(dict.fromkeys(names))
        self.fields['location'].widget = forms.Select(choices=[('', 'Seleccione o local')] + [(n, n) for n in names] + [('__new__', '+ Novo local')])
        self.fields['new_location'].help_text = 'Indique o nome e a localização. Ficará disponível nos próximos grupos.'
        # Preserve legacy resource references; do not accept forged hidden room values.
        self.fields['centre'].disabled = True
        self.fields['room'].disabled = True
        for f in self.fields.values():f.widget.attrs['class']='gf-field'
    @property
    def design_groups(self):return [('',self.visible_fields())]
    def clean_dates(self):
        raw=self.cleaned_data.get('dates','')
        if not raw:return []
        try:values=[date.fromisoformat(v.strip()) for v in raw.replace(',','\n').splitlines() if v.strip()]
        except ValueError:raise ValidationError('Use datas válidas no formato AAAA-MM-DD.')
        if not 1<=len(values)<=366 or len(values)!=len(set(values)):
            raise ValidationError('Indique 1 a 366 dias efectivos distintos.')
        return sorted(values)
    @property
    def total_hours(self):
        data=getattr(self,'cleaned_data',{}) if self.is_bound else self.initial
        dates=data.get('dates') or data.get('session_dates');hours=data.get('hours_per_day')
        if isinstance(dates,str):
            try:dates=[date.fromisoformat(v.strip()) for v in dates.replace(',','\n').splitlines() if v.strip()]
            except ValueError:return None
        return Decimal(len(set(dates)))*Decimal(hours) if dates and hours is not None else None
    def clean(self):
        data=super().clean()
        data['planning_confirmation'] = data.get('planning_confirmation') or 'Provisorio'
        location = data.get('location', '')
        if location == '__new__':
            location = ' '.join((data.get('new_location') or '').split())
            if not location:
                self.add_error('new_location', 'Indique o nome e a localização do novo local.')
        data['location'] = location
        if location and location != self.initial.get('location'):
            data['centre'] = None
            data['room'] = None
        explicit=data.get('dates')
        if explicit:
            data['start']=data.get('start') or explicit[0]
            data['end']=data.get('end') or explicit[-1]
        first, last = data.get('start'), data.get('end')
        if first and last:
            count = (last - first).days + 1
            if count < 1:
                self.add_error('end', 'A data de fim não pode ser anterior à data de início.')
            elif count > 366:
                self.add_error('end', 'A duração não pode ultrapassar 366 dias.')
            else:
                import re
                raw = data.get('session_dates','') or '\n'.join(d.isoformat() for d in data.get('dates',[]))
                try:
                    dates = [date.fromisoformat(x) for x in re.split(r'[\s,;]+', raw.strip()) if x]
                    if len(dates) != len(set(dates)): raise ValueError()
                    if any(d < first or d > last for d in dates): raise ValueError()
                    data['dates'] = sorted(dates)
                except ValueError:
                    self.add_error('session_dates','Indique dias efectivos distintos dentro do intervalo, em AAAA-MM-DD.')
        start=data.get('start_time');end=data.get('end_time')
        if start and end:
            duration=Decimal((end.hour*60+end.minute)-(start.hour*60+start.minute))/60
            if duration<=0:self.add_error('end_time','A hora de fim deve ser posterior à hora de início, no mesmo dia.')
            elif data.get('hours_per_day') is not None and data.get('break_hours') is not None and data['hours_per_day']+data['break_hours']>duration:
                self.add_error('hours_per_day','Horas de formação e pausas não podem ultrapassar o horário disponível.')
        if data.get('room') and data['room'].centre_id != getattr(data.get('centre'),'pk',None):
            self.add_error('room','A sala não pertence ao centro seleccionado.')
        check_submitted_total(self,self.total_hours)
        return data


def group_values(data):
    days=data['dates']
    if isinstance(data['trainer'], Trainer):
        data = {**data, 'trainer_record': data['trainer'], 'trainer': data['trainer'].name}
    return {**({'trainer_record': data['trainer_record']} if 'trainer_record' in data else {}), **{k:data[k] for k in ['trainer','provider','location','capacity','hours_per_day','break_hours','start_time','end_time']},'start':days[0],'end':days[-1],'days':len(days)}


def create_group(course, data):
    values = dict(data)
    dates = sorted(values.get('dates', []))
    if not dates:raise ValidationError('Indique os dias efectivos da turma.')
    if 'total_hours' in values:
        try:
            total=Decimal(str(values['total_hours']))
            if not total.is_finite() or total!=Decimal(len(dates))*Decimal(str(values['hours_per_day'])):raise ValueError
        except (ValueError,ArithmeticError):raise ValidationError('O total enviado diverge dos dias efectivos e horas por dia.')
    values.setdefault('start', dates[0] if dates else timezone.localdate())
    values.setdefault('end', dates[-1] if dates else values['start'])
    return save_group(course.pk, values)


def _legacy_history_rows(group, people):
    group.course = Course.objects.get(pk=group.course_id)
    ids=[p.pk for p in people]
    old=list(Training.objects.filter(employee_id__in=ids).select_related('group__course'))
    other=list(Enrollment.objects.filter(employee_id__in=ids,state='Activa',group__course=group.course).exclude(group=group).select_related('group'))
    existing=set(group.enrollments.filter(employee_id__in=ids).values_list('employee_id',flat=True))
    titles={normalise(t) for t in [group.course.title,*group.course.aliases,group.title]}
    by_person={p.pk:{'person':p,'warnings':[],'other':[],'blocked':p.pk in existing,'block_reason':'Já inscrito neste grupo.' if p.pk in existing else '', 'fingerprint':[]} for p in people}
    for row in old:
        same = row.group.course_id==group.course_id if row.group_id else (row.course_id==group.course_id if row.course_id else (normalise(row.title) in titles or bool(group.course.code and row.source_course_code and normalise(row.source_course_code)==normalise(group.course.code))))
        if not same:continue
        entry=by_person[row.employee_id]
        label=f'{row.group.code if row.group_id else "Registo anterior sem grupo"} · {row.start:%d/%m/%Y} · {row.status}'
        if row.attendance_confirmed and (row.attendance or 0)>0:kind='Participação anterior';note='Este formando já participou nesta formação. Consulte o histórico antes de efectuar uma nova inscrição.'
        elif row.status in ['Concluído','Desistência'] and row.attendance_confirmed and row.attendance==0:kind='Ausência confirmada';note='Existe um registo anterior desta formação, com zero presenças verificadas.'
        else:kind='Participação por confirmar';note='Não existem presenças verificadas que permitam confirmar a ausência.'
        entry['blocked'] = True
        entry['block_reason'] = 'Já tem histórico nesta formação. Não pode entrar noutro grupo da mesma formação.'
        entry['warnings'].append({'kind':kind,'note':note,'label':label})
        entry['fingerprint'].append(('t',row.pk,row.version,row.attendance,row.attendance_confirmed,row.status,row.group_id))
    for old_en in other:
        entry=by_person[old_en.employee_id]
        entry['other'].append(old_en)
        entry['blocked'] = True
        entry['block_reason'] = 'Já está inscrito noutro grupo desta formação.'
        entry['warnings'].append({'kind':'Inscrição noutro grupo','note':f'Já está inscrito no {old_en.group.code} ({old_en.group.state}).','label':old_en.group.title})
        entry['fingerprint'].append(('e',old_en.pk,old_en.state,old_en.group.revision,old_en.training_id))
    for entry in by_person.values():
        entry['key']=hashlib.sha256(json.dumps([entry['fingerprint'],entry['blocked']],sort_keys=True,default=str).encode()).hexdigest()
    return list(by_person.values())


def selected_people(request):
    raw=request.POST.getlist('employees')
    if not raw or len(raw)>1000 or any(not x.isdecimal() or len(x)>18 for x in raw):raise ValidationError('Seleccione entre 1 e 1.000 formandos válidos.')
    ids={int(x) for x in raw};people=list(Employee.objects.filter(pk__in=ids))
    if len(people)!=len(ids):raise ValidationError('Um formando já não está disponível. Refaça a selecção.')
    return people


@transaction.atomic
def enrol_reviewed(group, people, decisions, expected_keys, revision, actor=None):
    from .eligibility import enabled
    if enabled():return _secure_enrol_reviewed(group,people,decisions,expected_keys,revision,actor=actor)
    if TrainingGroup.objects.filter(pk=group.pk,state='Planeada',end__gte=timezone.localdate(),revision=revision).update(revision=F('revision')+1)!=1:
        raise ValidationError('O grupo foi alterado ou a data de fim já passou. Reveja a selecção.')
    group.refresh_from_db()
    entries=history_rows(group,people)
    if {str(x['person'].pk):x['key'] for x in entries}!=expected_keys:
        raise ValidationError('O histórico ou as inscrições mudaram. Reveja novamente os avisos.')
    accepted=[]
    for item in entries:
        pid=str(item['person'].pk);choice,reason=decisions[pid];reason=reason.strip()
        if choice=='skip':continue
        if item['blocked']:
            raise ValidationError(item['block_reason'])
        if choice != 'add':
            raise ValidationError('Apenas novas inscrições são permitidas. Não é possível repetir ou transferir a mesma formação.')
        if len(reason)>1000:
            raise ValidationError('O motivo não pode ultrapassar 1.000 caracteres.')
        accepted.append((item['person'],reason))
    if group.enrollments.filter(state='Activa').count()+len(accepted)>group.capacity:raise ValidationError('A selecção ultrapassa a capacidade do grupo. Retire formandos ou aumente a capacidade.')
    for person,reason in accepted:Enrollment.objects.create(group=group,employee=person,reason=reason)
    return len(accepted)


@login_required
def index(request):
    if request.GET.get('course','').isdigit():
        query=request.GET.copy();course_id=query.pop('course')[0]
        url=reverse('course_groups',args=[course_id])
        return redirect(url+('?' + query.urlencode() if query else ''))
    q=request.GET.get('q','').strip()
    all_courses=Course.objects.all()
    catalogue_stats={'total':all_courses.count(),'pending':all_courses.filter(validation_status__in=['pending','Por confirmar']).count(),'validated':all_courses.filter(validation_status__in=['validated','Validado']).count(),'blocked':all_courses.filter(validation_status__in=['blocked','Rever']).count()}
    courses=all_courses.annotate(group_count=Count('groups'))
    if q:courses=courses.filter(Q(title__icontains=q)|Q(area__icontains=q)|Q(code__icontains=q)|Q(internal_code__icontains=q)|Q(source_internal_code__icontains=q)|Q(source_action_id__icontains=q))
    selected_validation=request.GET.get('validation','')
    choices={'pending':['pending','Por confirmar'],'validated':['validated','Validado'],'blocked':['blocked','Rever']}
    if selected_validation in choices:courses=courses.filter(validation_status__in=choices[selected_validation])
    availability=request.GET.get('availability','')
    if availability in ('active','archived'):courses=courses.filter(active=availability=='active')
    selected_area=request.GET.get('area','')
    if selected_area=='__undefined__':courses=courses.filter(area='')
    elif selected_area:
        from .training_areas import normalize_area
        try:courses=courses.filter(area=normalize_area(selected_area,allow_blank=False))
        except ValueError:courses=courses.none()
    course_page=Paginator(courses.order_by('title','pk'),24).get_page(request.GET.get('course_page'))
    from .catalogue_technical import attach
    attach(course_page.object_list)
    from .group_numbers import attach as attach_numbers
    attach_numbers(course_page.object_list)
    from . import historical
    history_visible=historical.enabled() and request.user.has_perms(['training.view_training','training.view_employee'])
    if history_visible:historical.attach_counts(course_page.object_list)
    query=request.GET.copy();query.pop('course_page',None)
    course_query=query.urlencode()
    query=request.GET.copy();query.pop('page',None)
    group_query=query.urlencode()

    from .interface_groups import attach_course_counts
    visible_courses=attach_course_counts(course_page.object_list)
    from .catalogue_actions import attach as attach_actions
    attach_actions(visible_courses,request.user)
    from .course_codes import attach as attach_codes
    attach_codes(visible_courses)
    return render(request,'planning/index.html',{'courses':visible_courses,'course_page':course_page,'catalogue_stats':catalogue_stats,'history_visible':history_visible,'validation':selected_validation if selected_validation in choices else '', 'area':selected_area if selected_area in [*list(all_courses.values_list('area',flat=True)),'__undefined__'] else '', 'validation_choices':Course.ValidationState.choices,'catalogue_query':course_query,'areas':list(all_courses.exclude(area='').order_by('area').values_list('area',flat=True).distinct()),'q':q})

@login_required
@require_http_methods(['GET'])
def course_groups(request,pk):
    selected_course=get_object_or_404(Course,pk=pk)
    from .group_numbers import next_number
    selected_course.next_group_preview=next_number(selected_course)
    from .catalogue_actions import attach as attach_actions
    attach_actions([selected_course],request.user)
    from .course_codes import attach as attach_codes
    attach_codes([selected_course])
    q=request.GET.get('q','').strip()
    query=request.GET.copy();query.pop('page',None);query.pop('course',None)
    group_query=query.urlencode()
    groups=TrainingGroup.objects.select_related('course').annotate(active_count=Count('enrollments',filter=Q(enrollments__state='Activa')))
    if selected_course:
        groups = groups.filter(course=selected_course)
    if q:groups=groups.filter(Q(title__icontains=q)|Q(trainer__icontains=q)|Q(location__icontains=q)|Q(course__code__icontains=q))
    summary = groups.aggregate(total=Count('pk', distinct=True),
        confirmed=Count('pk', filter=Q(planning_confirmation='Confirmado') & ~Q(state='Cancelada'), distinct=True),
        provisional=Count('pk', filter=Q(planning_confirmation='Provisorio') & ~Q(state='Cancelada'), distinct=True),
        missing_room=Count('pk', filter=Q(room__isnull=True) & ~Q(state='Cancelada'), distinct=True))
    page = Paginator(groups.order_by('-start','-pk'),20).get_page(request.GET.get('page'))
    summaries = roster_summaries(page.object_list)
    for group in page.object_list:
        group.participant_count = summaries[group.pk]['participant_count']
        group.display_state = summaries[group.pk]['display_state']
        group.historical_only = summaries[group.pk]['historical_only']
        group.can_delete_preparation = group_deletion_allowed(group)
    from .interface_groups import extend
    context=extend({'courses':[],'planning_summary':summary,'page':page,'q':q,'selected_course':selected_course,'group_query':group_query},request)
    for visible_group in context['page'].object_list:
        visible_group.can_delete_preparation = not getattr(visible_group,'historical_edition',False) and group_deletion_allowed(visible_group)
    from . import group_validation as validation
    context['documentary_visible']=request.user.is_active and request.user.has_perms([validation.PERMISSION,'training.view_training','training.view_employee'])
    if context['documentary_visible']:
        from .documentary_readiness import attach as attach_readiness
        attach_readiness(context['page'].object_list,request.user)
    return render(request,'planning/course_groups.html',context)


@login_required
@staff_required
def course_edit(request,pk=None):
    course=get_object_or_404(Course,pk=pk) if pk else Course()
    from .catalogue_technical import attach
    attach([course])
    original_title=course.title;original_area=course.area;revision=course.revision
    form=CourseForm(request.POST if request.method=='POST' else None,instance=course,initial={'revision':revision})
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            if pk and form.cleaned_data['revision']!=revision:form.add_error(None,'O catálogo mudou. Reabra a acção antes de guardar.')
            else:
                item=form.save(commit=False);item.key=normalise(item.title);item.aliases=list(dict.fromkeys([*item.aliases,original_title,item.title]));item.revision=revision+1
                if pk:
                    values={f:form.cleaned_data[f] for f in form.Meta.fields};values.update(key=item.key,aliases=item.aliases,revision=item.revision)
                    try:
                        with transaction.atomic():
                            if Course.objects.filter(pk=pk,revision=revision).update(**values)!=1:
                                form.add_error(None,'A acção foi alterada noutra janela.')
                            else:
                                from .area_sync import propagate
                                from .models import TrainingAreaChange
                                propagate(Course,item)
                                if original_area!=item.area:
                                    TrainingAreaChange.objects.create(policy='catalogue-update',model_name='Course',object_id=str(item.pk),previous_area=original_area,new_area=item.area,reason='Decisão no catálogo por '+request.user.username)
                    except IntegrityError:
                        form.add_error(None,'O código ou título já pertence a outra acção. Reveja o catálogo.')
                else:
                    try:
                        with transaction.atomic():item.save()
                    except IntegrityError:form.add_error(None,'O código ou título já pertence a outra acção. Reveja o catálogo.')
                if not form.errors:
                    record(request,'PLANNING_COURSE',json.dumps({'course':item.pk,'total_hours':str(item.total_hours) if item.total_hours is not None else None,'duration_before':[form.duration_before[0],str(form.duration_before[1]) if form.duration_before[1] is not None else None],'duration_after':[item.default_days,str(item.default_hours) if item.default_hours is not None else None],'reference':form.cleaned_data.get('duration_reference'),'reason':form.cleaned_data.get('duration_reason')},ensure_ascii=False));return redirect('planning')
    return render(request,'planning/form.html',{'form':form,'title':'Editar acção de formação' if pk else 'Nova acção de formação','course':course})


@login_required
@staff_required
def group_edit(request,course_pk=None,pk=None):
    group=get_object_or_404(TrainingGroup,pk=pk) if pk else None
    if group and not request.user.has_perm('training.change_traininggroup'):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if group and group.trainings.exists():
        messages.error(request,'O histórico existente impede a alteração do calendário.')
        return redirect('group_detail',pk=group.pk)
    course=group.course if group else get_object_or_404(Course,pk=course_pk)
    from .catalogue_actions import creation_reason
    if not group and creation_reason(course,request.user):
        messages.error(request,creation_reason(course,request.user))
        return redirect('planning')
    if group and group.state!='Planeada':
        messages.error(request,'O calendário fica fechado após iniciar ou cancelar o grupo.')
        return redirect('group_detail',pk=group.pk)
    initial={'hours_per_day':course.default_hours or Decimal(7),'start_time':'08:00','end_time':'16:00','planning_confirmation':'Provisorio'}
    if not group:
        previous = course.groups.exclude(state='Cancelada').order_by('-number').first()
        if previous:
            initial.update({k: getattr(previous, k) for k in ['provider', 'location', 'capacity', 'hours_per_day', 'break_hours', 'start_time', 'end_time']})
            if previous.trainer_record_id and previous.trainer_record.active:
                initial['trainer'] = previous.trainer_record_id
    if group:
        initial.update({k:getattr(group,k) for k in ['provider','location','capacity','hours_per_day','break_hours','start_time','end_time','revision','start','end','planning_confirmation']})
        initial.update(trainer=group.trainer_record_id,centre=group.centre_id,room=group.room_id,
            session_dates='\n'.join(d.isoformat() for d in group.sessions.values_list('date',flat=True)))
    form=GroupForm(request.POST if request.method=='POST' else None,initial=initial,current_trainer=group.trainer_record_id if group else None)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                if group and not form.cleaned_data.get('correction_reason','').strip():
                    raise ValidationError('Indique o motivo da correcção.')
                before={k:str(getattr(group,k)) for k in ('location','trainer','start','end','hours_per_day','capacity')} if group else None
                group=save_group(course.pk,form.cleaned_data,group_id=pk,revision=form.cleaned_data.get('revision'),actor=request.user.username)
                if before is not None:
                    from .eligibility import enabled, validate_export
                    if enabled():validate_export(group,list(group.enrollments.filter(state='Activa').select_related('employee')))
                    after={k:str(getattr(group,k)) for k in before}
                    record(request,'PLANNING_GROUP_CORRECTION',json.dumps({'before':before,'after':after,'reason':form.cleaned_data['correction_reason']},ensure_ascii=False))
            for warning in group._planning_warnings: messages.warning(request,warning)
            messages.success(request,'Planeamento guardado; confirmação e estado operacional separados.')
            return redirect('group_detail',pk=group.pk)
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages))
        except OperationalError as exc:
            if 'locked' not in str(exc).lower(): raise
            return HttpResponse('Planeamento ocupado. Reabra a página e tente novamente.',status=409)
    from .group_numbers import next_number
    forecast=next_number(course) if not group else group.number
    return render(request,'planning/form.html',{'form':form,'title':f'Calendário — {group.code}' if group else f'Criar G{forecast}','course':course,'group':group})


def historical_group_source_keys(group_ids):
    return source_keys(group_ids)


def group_participant_counts(groups):
    return {pk: item['participant_count'] for pk, item in roster_summaries(groups).items()}


def historical_group_trainings(group):
    return group_trainings(group)


@login_required
def group_detail(request,pk):
    group=get_object_or_404(TrainingGroup.objects.select_related('course'),pk=pk)
    enrollments=group.enrollments.select_related('employee','training')
    active=enrollments.filter(state='Activa')
    source_trainings = historical_group_trainings(group)
    roster = roster_summaries([group])[group.pk]
    return render(request,'planning/group.html',{'attendance_open':group.state != 'Cancelada', 'attendance_second_copy':group.end < timezone.localdate() or group.state == 'Concluída', 'source_trainings':source_trainings,'group':group,'enrollments':enrollments,'active_count':active.count(),'participant_count':roster['participant_count'],'roster':roster,'can_delete_group':group_deletion_allowed(group),'sessions':group.sessions.all(),'planning_warnings':([conflict_message(c) for c in detect_conflicts(group,list(group.sessions.values_list('date',flat=True)))] + (['Grupo provisório; reserva não confirmada.'] if group.planning_confirmation!='Confirmado' else [])) if request.user.is_staff else [],'seed':group.trainings.order_by('pk').first() or source_trainings.first()})


@login_required
@staff_required
def enrol(request,pk):
    group=get_object_or_404(TrainingGroup.objects.select_related('course'),pk=pk)
    if group.state!='Planeada' or group.end < timezone.localdate():
        messages.error(request,'Seleccione formandos num grupo planeado cuja data de fim ainda não passou.')
        return redirect('group_detail',pk=pk)
    q=request.GET.get('q','').strip();direction=request.GET.get('direction','');people=Employee.objects.all()
    if q:people=people.filter(Q(pk__in=person_ids(q)))
    if direction:people=people.filter(direction=direction)
    error='';review=[];token=''
    if request.method=='POST':
        try:
            selected=selected_people(request);review=history_rows(group,selected)
            keys={str(x['person'].pk):x['key'] for x in review}
            if request.POST.get('action')=='confirm':
                payload=signing.loads(request.POST.get('review_token',''),salt='planning-enrol',max_age=1200)
                if payload!={'user':request.user.pk,'group':pk,'revision':group.revision,'keys':keys}:raise ValidationError('A selecção mudou ou a revisão expirou. Reveja os avisos novamente.')
                decisions={str(p.pk):(request.POST.get(f'choice_{p.pk}','skip'),request.POST.get(f'reason_{p.pk}','')) for p in selected}
                for item in review:item['choice'],item['reason']=decisions[str(item['person'].pk)]
                count=enrol_reviewed(group,selected,decisions,keys,payload['revision'],actor=request.user)
                record(request,'PLANNING_ENROL',f'{group.code}, acção {group.course_id}: {count} inscrições revistas.');messages.success(request,f'{count} formando(s) adicionado(s).');return redirect('group_detail',pk=pk)
            token=signing.dumps({'user':request.user.pk,'group':pk,'revision':group.revision,'keys':keys},salt='planning-enrol')
        except (ValidationError,signing.BadSignature,signing.SignatureExpired) as exc:
            error=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'A revisão expirou. Reveja novamente os formandos.'
            if review:token=signing.dumps({'user':request.user.pk,'group':pk,'revision':group.revision,'keys':{str(x['person'].pk):x['key'] for x in review}},salt='planning-enrol')
    eligible_ids = [entry['person'].pk for entry in history_rows(group, list(people)) if not entry['blocked']]
    people = people.filter(pk__in=eligible_ids)
    page=Paginator(people,30).get_page(request.GET.get('page'))
    return render(request,'planning/enrol.html',{'group':group,'page':page,'candidates':history_rows(group,list(page)),'directions':Employee.objects.exclude(direction='').values_list('direction',flat=True).distinct().order_by('direction'),'q':q,'direction':direction,'review':review,'review_token':token,'error':error})


@login_required
@staff_required
@require_POST
def transition(request,pk):
    try:
        with reservation_transaction():
            revision=request.POST.get('revision','');action=request.POST.get('action','')
            if not revision.isdecimal() or action not in ['start','finish','cancel']:raise ValidationError('Acção inválida.')
            # Reserve state/version before reading enrolled participants.
            current=get_object_or_404(TrainingGroup,pk=pk)
            expected='Em Curso' if action=='finish' else 'Planeada'
            if TrainingGroup.objects.filter(pk=pk,state=expected,revision=int(revision)).update(revision=F('revision')+1)!=1:raise ValidationError('O estado ou os participantes mudaram. Reabra o grupo.')
            current.refresh_from_db()
            if action=='start':
                if roster_summaries([current])[current.pk]['historical_only']:
                    raise ValidationError('Este grupo já tem histórico associado. Crie um novo grupo para uma nova realização da formação.')
                if current.planning_confirmation != 'Confirmado':raise ValidationError('Confirme o planeamento e os recursos antes de iniciar.')
                validate_reservation(current,list(current.sessions.values_list('date',flat=True)))
                if current.start>timezone.localdate():raise ValidationError('O grupo ainda não chegou à data de início.')
                active=list(current.enrollments.filter(state='Activa').select_related('employee'))
                if not active:raise ValidationError('Adicione pelo menos um formando antes de iniciar.')
                for en in active:
                    if en.training_id or Training.objects.filter(group=current, employee=en.employee).exists():
                        raise ValidationError('Já existe uma participação para este formando e grupo. Reveja a ligação antes de iniciar.')
                    t=Training(employee=en.employee,group=current,course=current.course,plan_status='No Plano',title=current.title,area=current.area,trainer=current.trainer,trainer_record=current.trainer_record,provider=current.provider,location=current.location,start=current.start,end=current.end,days=current.days,hours_per_day=current.hours_per_day,attendance=0,status='Em Curso',result='Pendente')
                    t.full_clean();t.save();en.training=t;en.full_clean();en.save(update_fields=['training'])
                current.state='Em Curso'
            elif action=='finish':
                if current.end>timezone.localdate():raise ValidationError('O grupo ainda não chegou à data final.')
                active = current.enrollments.filter(state='Activa')
                if not active.exists() or active.filter(training__isnull=True).exists():
                    raise ValidationError('Todas as inscrições activas devem ter uma participação antes de concluir.')
                if current.trainings.exclude(status__in=['Concluído','Desistência']).exists():raise ValidationError('Finalize os resultados de todos os formandos na grelha antes de concluir o grupo.')
                if current.trainings.filter(attendance_confirmed=False).exists():raise ValidationError('Verifique as presenças de todos os formandos, incluindo as ausências, antes de concluir.')
                current.state='Concluída'
            else:
                reason=request.POST.get('reason','').strip()
                if not reason:raise ValidationError('Indique o motivo do cancelamento.')
                current.state='Cancelada';current.enrollments.filter(state='Activa').update(state='Cancelada',reason='Grupo cancelado: '+reason[:1000])
            current.save(update_fields=['state']);record(request,'PLANNING_STATE',f'{current.code}, acção {current.course_id}: {current.state}')
        messages.success(request,'Estado do grupo actualizado.')
    except IntegrityError:
        messages.error(request, 'A operação entrou em conflito com uma participação existente. Reabra o grupo.')
    except ValidationError as exc:messages.error(request,' '.join(exc.messages))
    return redirect('group_detail',pk=pk)


@login_required
@staff_required
@require_POST
def enrollment_edit(request,pk,en_pk):
    try:
        with transaction.atomic():
            rev=request.POST.get('revision','')
            if not rev.isdecimal():raise ValidationError('Revisão inválida.')
            if TrainingGroup.objects.filter(pk=pk,revision=int(rev)).update(revision=F('revision')+1)!=1:raise ValidationError('O grupo mudou. Reabra a página.')
            group=get_object_or_404(TrainingGroup,pk=pk)
            en=get_object_or_404(Enrollment,pk=en_pk,group=group,state='Activa')
            if request.POST.get('action')=='cancel':
                if group.state!='Planeada' or en.training_id:raise ValidationError('Depois do início, registe a ausência ou desistência na grelha; o histórico é conservado.')
                reason=request.POST.get('reason','').strip()
                if not reason:raise ValidationError('Indique o motivo da retirada.')
                en.state='Cancelada';en.reason=reason[:1000]
            else:
                if group.state not in ['Planeada','Em Curso']:raise ValidationError('O grupo está fechado.')
                status=request.POST.get('confirmation','')
                if status not in dict(Enrollment._meta.get_field('confirmation').choices):raise ValidationError('Confirmação inválida.')
                en.confirmation=status;en.contact_note=request.POST.get('contact_note','')[:1000]
            en.save();record(request,'PLANNING_ENROL_EDIT',f'{group.code}: inscrição {en.pk}, {en.state}, {en.confirmation}.')
        messages.success(request,'Inscrição actualizada.')
    except ValidationError as exc:messages.error(request,' '.join(exc.messages))
    return redirect('group_detail',pk=pk)


@login_required
def attendance_pdf(request,pk):
    group=get_object_or_404(TrainingGroup,pk=pk)
    if group.state=='Cancelada':return HttpResponseBadRequest('O grupo está cancelado.')
    if request.GET.get('source') == 'historical':
        from types import SimpleNamespace
        from .attendance_pdf import build_attendance
        import io
        historical=list(historical_group_trainings(group))
        if not historical:return HttpResponseBadRequest('Este grupo não tem participantes históricos associados.')
        periods=sorted(set((t.start,t.end) for t in historical))
        if any(not a or not b for a,b in periods):return HttpResponseBadRequest('O histórico não contém um período completo.')
        rows=[];seen=set()
        for t in historical:
            if t.employee_id not in seen:
                rows.append(SimpleNamespace(employee=t.employee));seen.add(t.employee_id)
        period_label='; '.join(f'{a:%d/%m/%Y} - {b:%d/%m/%Y}' for a,b in periods)
        try:content=build_attendance(group,None,rows,second_copy=True,historical_period=period_label)
        except ValueError as exc:return HttpResponseBadRequest(str(exc))
        return FileResponse(io.BytesIO(content),as_attachment=False,filename=f'EPAL_Lista_Historica_{group.pk}.pdf',content_type='application/pdf')
    try:day=date.fromisoformat(request.GET.get('date',''))
    except ValueError:return HttpResponseBadRequest('Seleccione um dia do calendário.')
    if not group.sessions.filter(date=day).exists():return HttpResponseBadRequest('Este dia não pertence ao calendário do grupo.')
    rows=attendance_rows(group)
    if not rows:return HttpResponseBadRequest('O grupo ainda não tem formandos.')
    try:
        from .attendance_pdf import build_attendance
        import io
        content=build_attendance(group,day,rows,second_copy=group.end < timezone.localdate() or group.state == 'Concluída')
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    except ModuleNotFoundError as exc:
        if exc.name!='reportlab':raise
        return HttpResponseBadRequest('Instale a dependência PDF no mesmo terminal: python -m pip install reportlab==4.4.9. Depois reinicie a plataforma.')
    return FileResponse(io.BytesIO(content),as_attachment=False,filename=f'EPAL_Lista_{group.course_id}_{group.code}_{day:%Y%m%d}.pdf',content_type='application/pdf')


@login_required
def attendance_excel(request,pk):
    group=get_object_or_404(TrainingGroup,pk=pk)
    if group.state=='Cancelada':return HttpResponseBadRequest('O grupo está cancelado.')
    if request.GET.get('all')=='1':
        from .attendance_excel import daily_archive
        import copy,io
        days=list(group.sessions.values_list('date',flat=True))
        if not days:return HttpResponseBadRequest('Seleccione dias efectivos antes de exportar.')
        def render_day(day):
            single=copy.copy(request);single.GET=request.GET.copy();single.GET.pop('all',None);single.GET['date']=day.isoformat()
            response=attendance_excel(single,pk)
            if response.status_code!=200:raise ValidationError('Não foi possível gerar todos os dias.')
            try:return b''.join(response.streaming_content)
            finally:response.close()
        try:content=daily_archive(days,render_day,'EPAL-LISTA')
        except ValidationError:
            single=copy.copy(request);single.GET=request.GET.copy();single.GET.pop('all',None);single.GET['date']=days[0].isoformat()
            first=attendance_excel(single,pk)
            if first.status_code!=200:return first
            first.close()
            return HttpResponseBadRequest('Nenhuma lista gerada: falhou a geração de um dos dias.')
        return FileResponse(io.BytesIO(content),as_attachment=True,filename=f'EPAL-Lista-{group.pk}.zip',content_type='application/zip')

    if request.GET.get('source') == 'historical':
        from types import SimpleNamespace
        from .attendance_excel import build_attendance_excel
        import io
        historical=list(historical_group_trainings(group))
        if not historical:return HttpResponseBadRequest('Este grupo não tem participantes históricos associados.')
        if any(not t.start or not t.end for t in historical):
            return HttpResponseBadRequest('O histórico não contém um período completo.')
        periods=sorted(set((t.start,t.end) for t in historical))
        rows=[];seen=set()
        for t in historical:
            if t.employee_id not in seen:
                rows.append(SimpleNamespace(employee=t.employee));seen.add(t.employee_id)
        label='; '.join(f'{a:%d/%m/%Y} - {b:%d/%m/%Y}' for a,b in periods)
        try:content=build_attendance_excel(group,None,rows,second_copy=True,historical_period=label)
        except ValueError as exc:return HttpResponseBadRequest(str(exc))
        return FileResponse(io.BytesIO(content),as_attachment=True,
            filename=f'EPAL_Lista_Historica_{group.pk}.xlsx',
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    try:day=date.fromisoformat(request.GET.get('date',''))
    except ValueError:return HttpResponseBadRequest('Seleccione um dia do calendário.')
    if not group.sessions.filter(date=day).exists():return HttpResponseBadRequest('Este dia não pertence ao calendário do grupo.')
    rows=attendance_rows(group)
    if not rows:return HttpResponseBadRequest('O grupo ainda não tem formandos.')
    from .eligibility import enabled, validate_export
    if enabled():
        try:validate_export(group,list(group.enrollments.filter(state='Activa').select_related('employee')))
        except ValidationError as exc:
            from .eligibility import assess,require_admission
            blockers=[]
            for item in assess(group.course,[e.employee for e in group.enrollments.filter(state='Activa').select_related('employee')],group=group):
                item['blocked']=False
                try:require_admission(item)
                except ValidationError as reason:item['reason']=' '.join(reason.messages);blockers.append(item)
            return render(request,'planning/attendance_blocked.html',{'group':group,'errors':exc.messages,'blockers':blockers},status=400)
    from .attendance_excel import build_attendance_excel
    import io
    try:content=build_attendance_excel(group,day,rows,second_copy=group.end < timezone.localdate() or group.state == 'Concluída')
    except ValueError as exc:return HttpResponseBadRequest(str(exc))
    return FileResponse(io.BytesIO(content),as_attachment=True,filename=f'EPAL_Lista_{group.course_id}_{group.code}_{day:%Y%m%d}.xlsx',content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def group_deletion_allowed(group):
    if hasattr(group,'formation_survey'):return False
    if group.enrollments.filter(direct_assessment__isnull=False).exists() or hasattr(group, 'assessment_scale'):
        return False
    if hasattr(group,'planningdraft') or group.evaluation_cycles.exists():
        return False
    return group.state in ['Planeada','Cancelada'] and not group_trainings(group).exists()


def course_deletion_allowed(course):
    if course.source_system or course.source_action_id or course.consolidated_editions.exists():
        return False
    if course.development_actions.exists():
        return False
    if course.participations.exists():
        return False
    if course.groups.exists() or Training.objects.filter(group__course=course).exists():
        return False
    titles = {normalise(title) for title in [course.title, *course.aliases] if title}
    return not any(normalise(title) in titles for title in
                   Training.objects.filter(group__isnull=True).values_list('title', flat=True).iterator())


@login_required
@staff_required
@require_http_methods(['GET', 'POST'])
def course_delete(request, pk):
    course = get_object_or_404(Course, pk=pk)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                revision = request.POST.get('revision', '')
                reason = request.POST.get('reason', '').strip()
                if request.POST.get('confirm') != 'ELIMINAR':
                    raise ValidationError('Confirme a eliminação escrevendo ELIMINAR.')
                if not reason or len(reason) > 1000:
                    raise ValidationError('Indique um motivo com até 1.000 caracteres.')
                # Obtain the SQLite write lock before checking groups/history.
                if (not revision.isdecimal() or len(revision) > 10 or
                        Course.objects.filter(pk=pk, revision=int(revision)).update(revision=F('revision') + 1) != 1):
                    raise ValidationError('A acção foi alterada. Reveja a página de confirmação.')
                course.refresh_from_db()
                if not course_deletion_allowed(course):
                    raise ValidationError('Esta acção tem grupos ou histórico associado, incluindo títulos anteriores. Conserve os registos e arquive a acção em Editar acção.')
                label = f'Acção {course.pk}: {course.title}'
                course.delete()
                record(request, 'COURSE_DELETE', f'{label}. Motivo: {reason}')
            messages.success(request, 'Formação eliminada do catálogo. Os técnicos e os outros registos foram conservados.')
            return redirect('planning')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('course_delete', pk=pk)
    return render(request, 'planning/course_delete.html', {
        'course': course, 'can_delete': course_deletion_allowed(course),
        'group_count': course.groups.count(),
    })


@login_required
@staff_required
@require_http_methods(['GET','POST'])
def group_delete(request, pk):
    group=get_object_or_404(TrainingGroup.objects.select_related('course'),pk=pk)
    if request.method=='POST':
        try:
            with reservation_transaction(allow_session_deletion=True):
                revision=request.POST.get('revision','')
                if not revision.isdecimal() or request.POST.get('confirm')!='ELIMINAR':
                    raise ValidationError('Confirme a eliminação escrevendo ELIMINAR.')
                if not request.POST.get('reason','').strip():raise ValidationError('Indique o motivo da eliminação.')
                if TrainingGroup.objects.filter(pk=pk,revision=int(revision)).update(revision=F('revision')+1)!=1:
                    raise ValidationError('O grupo foi alterado. Reveja a página de confirmação.')
                group.refresh_from_db()
                if not group_deletion_allowed(group):
                    raise ValidationError('Só é possível eliminar grupos planeados ou canceladas sem registos de formação associados.')
                label=f'{group.code}, acção {group.course_id}, grupo {group.pk}'
                count=group.enrollments.count()
                group.enrollments.all().delete()
                group.sessions.all().delete()
                group.delete()
                record(request,'PLANNING_DELETE',f'{label}: eliminada com {count} inscrições preparatórias. Motivo: {request.POST["reason"].strip()[:1000]}')
            messages.success(request,'Grupo eliminado. Os dados pessoais dos técnicos e o catálogo foram conservados. O código do grupo não será reutilizado.')
            return redirect('planning')
        except ValidationError as exc:messages.error(request,' '.join(exc.messages));return redirect('group_delete',pk=pk)
    return render(request,'planning/delete.html',{'group':group,'can_delete':group_deletion_allowed(group),
        'enrollment_count':group.enrollments.count(),'session_count':group.sessions.count()})


def history_rows(group, people):
    from .eligibility import enabled, assess
    return assess(group.course,people,group=group) if enabled() else _legacy_history_rows(group,people)


def _secure_enrol_reviewed(group, people, decisions, expected_keys, revision, actor=None):
    from .eligibility import enabled, require_admission
    secure=enabled()
    if secure:
        if not actor or not actor.is_active or not actor.is_staff or not actor.has_perm('training.add_enrollment'):
            raise ValidationError('Sem permissão para confirmar inscrições.')
        if not group.course.can_create_groups:
            raise ValidationError('A forma\u00e7\u00e3o est\u00e1 arquivada ou marcada para revis\u00e3o.')
        # One course lock serialises inscriptions into different groups too.
        if Course.objects.filter(pk=group.course_id,active=True).exclude(validation_status__in=['blocked','Rever']).update(next_group=F('next_group'))!=1:
            raise ValidationError('A formação deve estar disponível.')
    if TrainingGroup.objects.filter(pk=group.pk,state='Planeada',revision=revision).update(revision=F('revision')+1)!=1:
        raise ValidationError('O grupo foi alterado. Reveja novamente a selecção.')
    group.refresh_from_db()
    entries=history_rows(group,people)
    if {str(x['person'].pk):x['key'] for x in entries}!=expected_keys:
        raise ValidationError('O histórico ou as inscrições mudaram. Reveja novamente os avisos.')
    accepted=[];bumped=set()
    for item in entries:
        pid=str(item['person'].pk);choice,reason=decisions[pid];reason=reason.strip()
        if choice=='skip':continue
        if secure:require_admission(item,choice)
        if item['blocked']:raise ValidationError('O formando já consta deste grupo. Não pode ser inscrito novamente.')
        if choice not in ['add','repeat','transfer']:raise ValidationError('Escolha uma opção válida para cada formando.')
        if item['needs_reason'] and choice not in ['repeat','transfer']:raise ValidationError('Reveja os avisos e escolha repetir ou transferir.')
        if (item['needs_reason'] or choice in ['repeat','transfer']) and not reason:raise ValidationError('Indique o motivo da repetição ou transferência.')
        if len(reason)>1000:raise ValidationError('O motivo não pode ultrapassar 1.000 caracteres.')
        if choice=='transfer':
            if not item['can_transfer']:raise ValidationError('Só é possível transferir inscrições de grupos planeados, sem registo de participação. Nos restantes casos, mantenha o histórico e justifique uma nova inscrição.')
            for old in item['other']:
                if old.group_id not in bumped:
                    if TrainingGroup.objects.filter(pk=old.group_id,state='Planeada',revision=old.group.revision).update(revision=F('revision')+1)!=1:raise ValidationError('O grupo de origem mudou. Reveja a transferência.')
                    bumped.add(old.group_id)
                old.state='Transferida';old.reason=f'Transferida para {group.code}. {reason}';old.save(update_fields=['state','reason'])
        accepted.append((item['person'],reason))
    if group.enrollments.filter(state='Activa').count()+len(accepted)>group.capacity:raise ValidationError('A selecção ultrapassa a capacidade do grupo. Retire formandos ou aumente a capacidade.')
    for person,reason in accepted:Enrollment.objects.create(group=group,employee=person,reason=reason)
    if secure:
        from .models import AuditEvent
        AuditEvent.objects.create(actor=actor.get_username(),action='PLANNING_ENROL_SECURE',detail=json.dumps({
            'group':group.pk,'employees':[p.pk for p,_ in accepted],'decisions':decisions,
            'authorizations':[x['authorization'].pk for x in entries if x.get('authorization')],
            'revision':group.revision},default=str,ensure_ascii=False))
    return len(accepted)

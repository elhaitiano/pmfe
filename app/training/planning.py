"""Catalogue, numbered groups and reviewed enrolments. No attendance is inferred."""
import hashlib, json, unicodedata
from datetime import date, timedelta
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import F, Q, Count
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from .forms import StyledModelForm
from .models import Course, TrainingGroup, GroupSession, Enrollment, Employee, Training
from .views import staff_required, record
from .widgets import numeric_text_widgets, date_widgets
from .models import AuditEvent, Trainer


def normalise(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


class CourseForm(StyledModelForm):
    revision = forms.IntegerField(widget=forms.HiddenInput, required=False)
    class Meta:
        model = Course
        fields = ['code','title','area','contents','default_days','default_hours','validation_status','active']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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


class GroupForm(forms.Form):
    start = forms.DateField(label='Data de início', input_formats=['%d/%m/%Y', '%Y-%m-%d'], widget=forms.DateInput(format='%d/%m/%Y', attrs={'placeholder': 'dd/mm/aaaa', 'inputmode': 'numeric', 'maxlength': '10', 'pattern': '[0-9]{2}/[0-9]{2}/[0-9]{4}'}))
    end = forms.DateField(label='Data de fim', input_formats=['%d/%m/%Y', '%Y-%m-%d'], widget=forms.DateInput(format='%d/%m/%Y', attrs={'placeholder': 'dd/mm/aaaa', 'inputmode': 'numeric', 'maxlength': '10', 'pattern': '[0-9]{2}/[0-9]{2}/[0-9]{4}'}))
    trainer = forms.ModelChoiceField(label='Formador(a)', queryset=Trainer.objects.none(), empty_label='Seleccione o formador')
    provider = forms.CharField(label='Entidade formadora',max_length=200,required=False)
    location = forms.CharField(label='Local',max_length=200)
    capacity = forms.IntegerField(label='Capacidade',min_value=1,max_value=1000,initial=30)
    hours_per_day = forms.DecimalField(label='Horas de formação por dia',min_value=Decimal('.5'),max_value=24,max_digits=4,decimal_places=2)
    break_hours = forms.DecimalField(label='Pausas por dia (horas)',min_value=0,max_value=12,max_digits=4,decimal_places=2,initial=0)
    start_time = forms.TimeField(label='Hora de início',widget=forms.TimeInput(attrs={'type':'time'}))
    end_time = forms.TimeField(label='Hora de fim',widget=forms.TimeInput(attrs={'type':'time'}))
    revision = forms.IntegerField(widget=forms.HiddenInput,required=False)
    def __init__(self,*a,**kw):
        current_trainer = kw.pop('current_trainer', None)
        super().__init__(*a,**kw)
        self.fields['trainer'].queryset = Trainer.objects.filter(Q(active=True) | Q(pk=current_trainer)).order_by('name', 'pk')
        numeric_text_widgets(self.fields)
        date_widgets(self.fields)
        self.fields['location'].widget.attrs['list'] = 'training-sites'
        for f in self.fields.values():f.widget.attrs['class']='gf-field'
    @property
    def design_groups(self):return [('',self.visible_fields())]
    def clean(self):
        data=super().clean()
        first, last = data.get('start'), data.get('end')
        if first and last:
            count = (last - first).days + 1
            if count < 1:
                self.add_error('end', 'A data de fim não pode ser anterior à data de início.')
            elif count > 366:
                self.add_error('end', 'A duração não pode ultrapassar 366 dias.')
            else:
                data['dates'] = [first + timedelta(days=i) for i in range(count)]
        start=data.get('start_time');end=data.get('end_time')
        if start and end:
            duration=Decimal((end.hour*60+end.minute)-(start.hour*60+start.minute))/60
            if duration<=0:self.add_error('end_time','A hora de fim deve ser posterior à hora de início, no mesmo dia.')
            elif data.get('hours_per_day') is not None and data.get('break_hours') is not None and data['hours_per_day']+data['break_hours']>duration:
                self.add_error('hours_per_day','Horas de formação e pausas não podem ultrapassar o horário disponível.')
        return data


def group_values(data):
    days=data['dates']
    if isinstance(data['trainer'], Trainer):
        data = {**data, 'trainer_record': data['trainer'], 'trainer': data['trainer'].name}
    return {**({'trainer_record': data['trainer_record']} if 'trainer_record' in data else {}), **{k:data[k] for k in ['trainer','provider','location','capacity','hours_per_day','break_hours','start_time','end_time']},'start':days[0],'end':days[-1],'days':len(days)}


@transaction.atomic
def create_group(course, data):
    # Write first: serialises the allocator on SQLite as well as row-locking databases.
    if Course.objects.filter(pk=course.pk,active=True).update(next_group=F('next_group')+1)!=1:
        raise ValidationError('A acção de formação deixou de estar disponível.')
    course.refresh_from_db()
    group=TrainingGroup.objects.create(course=course,number=course.next_group-1,title=course.title,area=course.area,**group_values(data))
    GroupSession.objects.bulk_create([GroupSession(group=group,date=d) for d in data['dates']])
    return group


def history_rows(group, people):
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
        if row.attendance>0:kind='Participação anterior';note='Já participou nesta acção de formação.'
        elif row.status in ['Concluído','Desistência'] and row.attendance_confirmed:kind='Ausência confirmada';note='Existe um registo anterior desta formação, com zero presenças verificadas.'
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
def enrol_reviewed(group, people, decisions, expected_keys, revision):
    if TrainingGroup.objects.filter(pk=group.pk,state='Planeada',end__gte=timezone.localdate(),revision=revision).update(revision=F('revision')+1)!=1:
        raise ValidationError('A turma foi alterada ou a data de fim já passou. Reveja a selecção.')
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
    q=request.GET.get('q','').strip()
    courses=Course.objects.annotate(group_count=Count('groups'))
    if q:courses=courses.filter(Q(title__icontains=q)|Q(area__icontains=q)|Q(code__icontains=q))
    groups=TrainingGroup.objects.select_related('course').annotate(active_count=Count('enrollments',filter=Q(enrollments__state='Activa')))
    selected_course = get_object_or_404(Course, pk=request.GET['course']) if request.GET.get('course', '').isdigit() else None
    if selected_course:
        groups = groups.filter(course=selected_course)
    if q:groups=groups.filter(Q(title__icontains=q)|Q(trainer__icontains=q)|Q(location__icontains=q)|Q(course__code__icontains=q))
    return render(request,'planning/index.html',{'courses':courses.order_by('title','pk'),'page':Paginator(groups.order_by('-start','-pk'),20).get_page(request.GET.get('page')),'q':q,'selected_course':selected_course})


@login_required
@staff_required
def course_edit(request,pk=None):
    course=get_object_or_404(Course,pk=pk) if pk else Course()
    original_title=course.title;revision=course.revision
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
                    except IntegrityError:
                        form.add_error(None,'O código ou título já pertence a outra acção. Reveja o catálogo.')
                else:
                    try:
                        with transaction.atomic():item.save()
                    except IntegrityError:form.add_error(None,'O código ou título já pertence a outra acção. Reveja o catálogo.')
                if not form.errors:
                    record(request,'PLANNING_COURSE',f'Acção {item.pk}: {item.title}');return redirect('planning')
    return render(request,'planning/form.html',{'form':form,'title':'Editar acção de formação' if pk else 'Nova acção de formação','course':course})


@login_required
@staff_required
def group_edit(request,course_pk=None,pk=None):
    group=get_object_or_404(TrainingGroup,pk=pk) if pk else None
    course=group.course if group else get_object_or_404(Course,pk=course_pk)
    if group and group.state!='Planeada':messages.error(request,'O calendário fica fechado após iniciar ou cancelar o grupo.');return redirect('group_detail',pk=group.pk)
    initial={'hours_per_day':course.default_hours,'start_time':'08:00','end_time':'16:00'}
    if group:initial.update({k:getattr(group,k) for k in ['trainer','provider','location','capacity','hours_per_day','break_hours','start_time','end_time','revision']});initial['dates']='\n'.join(d.isoformat() for d in group.sessions.values_list('date',flat=True))
    if group:
        initial['trainer'] = group.trainer_record_id
        initial['start'] = group.start
        initial['end'] = group.end
    form=GroupForm(request.POST if request.method=='POST' else None, initial=initial,
                   current_trainer=group.trainer_record_id if group else None)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                if group:
                    if form.cleaned_data['capacity']<group.enrollments.filter(state='Activa').count():raise ValidationError('A capacidade não pode ser inferior ao número de inscritos activos.')
                    if TrainingGroup.objects.filter(pk=pk,revision=form.cleaned_data['revision'],state='Planeada').update(**group_values(form.cleaned_data),revision=F('revision')+1)!=1:raise ValidationError('O grupo foi alterado. Reabra o calendário.')
                    group.sessions.all().delete();GroupSession.objects.bulk_create([GroupSession(group=group,date=d) for d in form.cleaned_data['dates']])
                else:group=create_group(course,form.cleaned_data)
                record(request,'PLANNING_GROUP',f'Acção {course.pk}, {group.code}: calendário guardado.')
                return redirect('group_detail',pk=group.pk)
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages))
    return render(request,'planning/form.html',{'form':form,'title':f'Calendário — {group.code}' if group else 'Criar grupo (numeração automática)','course':course, 'group':group})


@login_required
def group_detail(request,pk):
    group=get_object_or_404(TrainingGroup.objects.select_related('course'),pk=pk)
    enrollments=group.enrollments.select_related('employee','training')
    active=enrollments.filter(state='Activa')
    source_ids = []
    for event in AuditEvent.objects.filter(action='IMPORT_GROUPS_70_V1').only('detail'):
        try:
            payload = json.loads(event.detail)
        except (ValueError, TypeError):
            continue
        if payload.get('group_id') == group.pk:
            source_ids.extend('pilar3b:' + value.strip() for value in
                              payload.get('source', {}).get('IDs de participação', '').split('|') if value.strip())
    source_trainings = Training.objects.filter(source_key__in=source_ids).select_related('employee').order_by('employee__name')
    return render(request,'planning/group.html',{'attendance_open':group.end >= timezone.localdate() and group.state not in ['Cancelada','Concluída'], 'source_trainings':source_trainings,'group':group,'enrollments':enrollments,'active_count':active.count(),'sessions':group.sessions.all(),'seed':group.trainings.order_by('pk').first()})


@login_required
@staff_required
def enrol(request,pk):
    group=get_object_or_404(TrainingGroup.objects.select_related('course'),pk=pk)
    if group.state!='Planeada' or group.end < timezone.localdate():
        messages.error(request,'Seleccione formandos num grupo planeado cuja data de fim ainda não passou.')
        return redirect('group_detail',pk=pk)
    q=request.GET.get('q','').strip();direction=request.GET.get('direction','');people=Employee.objects.all()
    if q:people=people.filter(Q(name__icontains=q)|Q(number__icontains=q))
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
                count=enrol_reviewed(group,selected,decisions,keys,payload['revision'])
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
        with transaction.atomic():
            revision=request.POST.get('revision','');action=request.POST.get('action','')
            if not revision.isdecimal() or action not in ['start','finish','cancel']:raise ValidationError('Acção inválida.')
            # Reserve state/version before reading enrolled participants.
            current=get_object_or_404(TrainingGroup,pk=pk)
            expected='Em Curso' if action=='finish' else 'Planeada'
            if TrainingGroup.objects.filter(pk=pk,state=expected,revision=int(revision)).update(revision=F('revision')+1)!=1:raise ValidationError('O estado ou os participantes mudaram. Reabra o grupo.')
            current.refresh_from_db()
            if action=='start':
                if current.start>timezone.localdate():raise ValidationError('O grupo ainda não chegou à data de início.')
                active=list(current.enrollments.filter(state='Activa').select_related('employee'))
                if not active:raise ValidationError('Adicione pelo menos um formando antes de iniciar.')
                for en in active:
                    if en.training_id or Training.objects.filter(group=current, employee=en.employee).exists():
                        raise ValidationError('Já existe uma participação para este formando e turma. Reveja a ligação antes de iniciar.')
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
        messages.error(request, 'A operação entrou em conflito com uma participação existente. Reabra a turma.')
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
    if group.end < timezone.localdate() or group.state == 'Concluída':
        return HttpResponseBadRequest('A lista de presença só pode ser gerada até à data de fim de um grupo não concluído.')
    try:day=date.fromisoformat(request.GET.get('date',''))
    except ValueError:return HttpResponseBadRequest('Seleccione um dia do calendário.')
    if not group.sessions.filter(date=day).exists():return HttpResponseBadRequest('Este dia não pertence ao calendário do grupo.')
    rows=list(group.enrollments.filter(state='Activa').select_related('employee'))
    if not rows:return HttpResponseBadRequest('O grupo ainda não tem formandos.')
    try:
        from .attendance_pdf import build_attendance
        import io
        content=build_attendance(group,day,rows)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    except ModuleNotFoundError as exc:
        if exc.name!='reportlab':raise
        return HttpResponseBadRequest('Instale a dependência PDF no mesmo terminal: python -m pip install reportlab==4.4.9. Depois reinicie a plataforma.')
    return FileResponse(io.BytesIO(content),as_attachment=True,filename=f'EPAL_Lista_{group.course_id}_{group.code}_{day:%Y%m%d}.pdf',content_type='application/pdf')


@login_required
def attendance_excel(request,pk):
    group=get_object_or_404(TrainingGroup,pk=pk)
    if group.state=='Cancelada':return HttpResponseBadRequest('O grupo está cancelado.')
    if group.end < timezone.localdate() or group.state == 'Concluída':
        return HttpResponseBadRequest('A lista de presença só pode ser gerada até à data de fim de um grupo não concluído.')
    try:day=date.fromisoformat(request.GET.get('date',''))
    except ValueError:return HttpResponseBadRequest('Seleccione um dia do calendário.')
    if not group.sessions.filter(date=day).exists():return HttpResponseBadRequest('Este dia não pertence ao calendário do grupo.')
    rows=list(group.enrollments.filter(state='Activa').select_related('employee'))
    if not rows:return HttpResponseBadRequest('O grupo ainda não tem formandos.')
    from .attendance_excel import build_attendance_excel
    import io
    try:content=build_attendance_excel(group,day,rows)
    except ValueError as exc:return HttpResponseBadRequest(str(exc))
    return FileResponse(io.BytesIO(content),as_attachment=True,filename=f'EPAL_Lista_{group.course_id}_{group.code}_{day:%Y%m%d}.xlsx',content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def group_deletion_allowed(group):
    if group.evaluation_cycles.exists():
        return False
    return group.state in ['Planeada','Cancelada'] and not group.trainings.exists()


def course_deletion_allowed(course):
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
                    raise ValidationError('Esta acção tem turmas ou histórico associado, incluindo títulos anteriores. Conserve os registos e arquive a acção em Editar acção.')
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
            with transaction.atomic():
                revision=request.POST.get('revision','')
                if not revision.isdecimal() or request.POST.get('confirm')!='ELIMINAR':
                    raise ValidationError('Confirme a eliminação escrevendo ELIMINAR.')
                if not request.POST.get('reason','').strip():raise ValidationError('Indique o motivo da eliminação.')
                if TrainingGroup.objects.filter(pk=pk,revision=int(revision)).update(revision=F('revision')+1)!=1:
                    raise ValidationError('A turma foi alterada. Reveja a página de confirmação.')
                group.refresh_from_db()
                if not group_deletion_allowed(group):
                    raise ValidationError('Só é possível eliminar turmas planeadas ou canceladas sem registos de formação associados.')
                label=f'{group.code}, acção {group.course_id}, turma {group.pk}'
                count=group.enrollments.count()
                group.enrollments.all().delete()
                group.sessions.all().delete()
                group.delete()
                record(request,'PLANNING_DELETE',f'{label}: eliminada com {count} inscrições preparatórias. Motivo: {request.POST["reason"].strip()[:1000]}')
            messages.success(request,'Turma eliminada. Os dados pessoais dos técnicos e o catálogo foram conservados. O código do grupo não será reutilizado.')
            return redirect('planning')
        except ValidationError as exc:messages.error(request,' '.join(exc.messages));return redirect('group_delete',pk=pk)
    return render(request,'planning/delete.html',{'group':group,'can_delete':group_deletion_allowed(group),
        'enrollment_count':group.enrollments.count(),'session_count':group.sessions.count()})

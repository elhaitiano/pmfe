"""Reviewed attendance imports into existing groups; no new people or duplicate rows."""
import hashlib,io,json,re,unicodedata,zipfile
from datetime import date,datetime
from decimal import Decimal,InvalidOperation
from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required,permission_required
from django.core import signing
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import IntegrityError,OperationalError
from django.db.models import F
from django.http import FileResponse,Http404
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Employee,TrainingGroup,Training,Enrollment,DocumentaryRecord,AuditEvent
from .scheduling_guard import reservation_transaction

SALT='pmfe.clean-attendance.v1'
PERMISSIONS=['training.view_employee','training.view_training','training.add_training','training.add_enrollment','training.change_traininggroup']
MAX_BYTES=5*1024*1024
def canonical(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',str(value or '').casefold()) if not unicodedata.combining(c)).split())
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def number(value):
    if isinstance(value,bool) or value is None:raise ValidationError('Indique o número mecanográfico.')
    text=str(value).strip()
    if re.fullmatch(r'\d+(?:\.0+)?',text):return str(int(Decimal(text)))
    if re.fullmatch(r'[A-Za-z0-9-]{1,40}',text):return text.upper()
    raise ValidationError('Número mecanográfico inválido.')
def authorize(user):
    if not getattr(settings,'PMF_ATTENDANCE_IMPORT_ENABLED',False):raise Http404
    if not user.is_authenticated or not user.is_active or not user.is_staff or not user.has_perms(PERMISSIONS):raise PermissionDenied
def dates(group):return list(group.sessions.order_by('date').values_list('date',flat=True))
def identity(group):
    return {'group':group.pk,'course':group.course_id,'title':group.course.title,'code':group.course.code,'dates':[d.isoformat() for d in dates(group)],'hours':str(group.hours_per_day.normalize()),'revision':group.revision,'state':group.state}
def validate_group(group):
    if group.state=='Cancelada' or not group.course.active:raise ValidationError('Este grupo está cancelado ou a formação está arquivada.')
    if not group.course.can_create_groups:raise ValidationError('A formação está arquivada ou marcada para revisão.')
    if not dates(group):raise ValidationError('Defina os dias de formação no grupo antes de importar.')
def mark(value):
    text=canonical(value)
    if text in ['p','1','1.0','presente','sim']:return 'P'
    if text in ['a','f','0','0.0','ausente','falta','nao']:return 'A'
    raise ValidationError('Use P para presença e A para ausência em todos os dias.')
def certificate(value):
    text=canonical(value)
    if text in ['', 'por confirmar']:return None
    if text in ['sim','s','1','1.0']:return True
    if text in ['nao','n','0','0.0']:return False
    raise ValidationError('Certificado obtido: indique Sim, Não ou deixe em branco se não souber.')
def safe_cell(cell):
    if cell.data_type=='f':raise ValidationError('Não use fórmulas nos campos de preenchimento: '+cell.coordinate+'.')
    return cell.value
def row_facts(row,hours):
    present=row['marks'].count('P');total=len(row['marks'])
    return {'presences':present,'absences':total-present,'attendance':str((Decimal(present)*100/total).quantize(Decimal('.01'))),'hours':str(Decimal(present)*Decimal(hours)),'certificate_obtained':row['certificate']}
def parse_file(content,group):
    validate_group(group)
    if not content or len(content)>MAX_BYTES:raise ValidationError('Envie um ficheiro Excel .xlsx até 5 MB.')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names=z.namelist()
            if len(names)!=len(set(names)) or sum(i.file_size for i in z.infolist())>40*1024*1024 or any('vbaproject' in n.casefold() or n.startswith('xl/externalLinks/') for n in names):raise ValidationError('O Excel contém conteúdo não suportado.')
        from openpyxl import load_workbook
        wb=load_workbook(io.BytesIO(content),read_only=False,data_only=False,keep_links=False)
    except ValidationError:raise
    except Exception:raise ValidationError('Não foi possível ler o Excel. Utilize o modelo disponível neste grupo.')
    try:
        if 'Formação' not in wb or 'Assiduidade' not in wb:raise ValidationError('Utilize o modelo com as folhas Formação e Assiduidade.')
        cfg=wb['Formação'];sheet=wb['Assiduidade']
        if canonical(safe_cell(cfg['B3']))!=canonical(group.course.title) or canonical(safe_cell(cfg['B4']))!=canonical(group.course.code):raise ValidationError('A formação do Excel não corresponde ao grupo seleccionado.')
        if canonical(safe_cell(cfg['B5'])).removeprefix('g')!=str(group.number):raise ValidationError('O número do grupo no Excel não corresponde ao grupo seleccionado.')
        try:hours=Decimal(str(safe_cell(cfg['B6']) or '7').replace(',','.'))
        except (InvalidOperation,ValueError):raise ValidationError('Indique horas por dia válidas, por exemplo 7 ou 6.')
        if not hours.is_finite() or not Decimal('.5')<=hours<=24 or hours.as_tuple().exponent < -2:raise ValidationError('Use entre 0,5 e 24 horas por dia, com até duas casas decimais.')
        expected=dates(group);count=len(expected);positions={}
        for col in range(3,3+count):
            value=safe_cell(sheet.cell(4,col))
            if isinstance(value,datetime):value=value.date()
            if not isinstance(value,date):
                for fmt in ['%Y-%m-%d','%d/%m/%Y']:
                    try:value=datetime.strptime(str(value),fmt).date();break
                    except ValueError:pass
            if not isinstance(value,date) or value in positions:raise ValidationError('Os cabeçalhos devem conter dias de formação distintos.')
            positions[value]=col
        if set(positions)!=set(expected):raise ValidationError('Os dias do Excel não coincidem com o calendário deste grupo.')
        if canonical(safe_cell(sheet.cell(4,3+count)))!='certificado obtido':raise ValidationError('As colunas do modelo foram alteradas. Descarregue um novo modelo.')
        if sheet.max_row>1515 or sheet.max_column>380:raise ValidationError('O Excel ultrapassa os limites do modelo.')
        official={}
        for person in Employee.objects.all():
            key=number(person.number)
            if key in official:raise ValidationError('Há números mecanográficos ambíguos no cadastro. Contacte o administrador.')
            official[key]=person
        result=[];seen=set()
        common_reference=str(safe_cell(cfg['B7']) or '').strip()
        for row_idx in range(5,sheet.max_row+1):
            values=[safe_cell(sheet.cell(row_idx,c)) for c in range(1,5+count)]
            if all(v is None or str(v).strip()=='' for v in values):continue
            try:
                key=number(values[0])
                if key in seen:raise ValidationError('Funcionário repetido no mesmo Excel.')
                person=official.get(key)
                if person is None:raise ValidationError('O número não pertence ao cadastro oficial. Não será criado um funcionário novo.')
                if values[1] and canonical(values[1])!=canonical(person.name):raise ValidationError('O nome não corresponde ao número mecanográfico; confirme ou deixe o nome em branco.')
                marks=[mark(safe_cell(sheet.cell(row_idx,positions[d]))) for d in expected]
                if any(d>timezone.localdate() for d in expected):raise ValidationError('Não registe presenças ou ausências em dias futuros.')
                obtained=certificate(safe_cell(sheet.cell(row_idx,3+count)))
                reference=str(safe_cell(sheet.cell(row_idx,4+count)) or common_reference).strip()
                if obtained and (not reference or not any(m=='P' for m in marks)):raise ValidationError('Um certificado obtido exige referência administrativa e pelo menos uma presença; resolva eventuais conflitos antes de importar.')
                if len(reference)>150:raise ValidationError('A referência do certificado deve ter até 150 caracteres.')
                item={'employee':person.pk,'number':person.number,'marks':marks,'certificate':obtained,'reference':reference if obtained else ''}
                result.append(item);seen.add(key)
            except ValidationError as exc:raise ValidationError(f'Linha {row_idx}: '+' '.join(exc.messages))
        if not result:raise ValidationError('Preencha pelo menos um funcionário com P ou A em todos os dias.')
        if len(result)>group.capacity:raise ValidationError('O número de funcionários ultrapassa a capacidade do grupo.')
        payload={'group':group.pk,'course':group.course_id,'dates':[d.isoformat() for d in expected],'hours':str(hours.normalize()),'rows':sorted(result,key=lambda r:r['employee']),'source_sha256':hashlib.sha256(content).hexdigest()}
        semantic={k:v for k,v in payload.items() if k!='source_sha256'};payload['digest']=digest(semantic)
        return payload
    finally:wb.close()

def validate_existing(group,payload):
    existing={t.employee_id:t for t in Training.objects.filter(group=group)};new=[];same=[]
    for row in payload['rows']:
        old=existing.get(row['employee'])
        if old:
            marker=(old.source_data or {}).get('_attendance_import_v1')
            if not marker or marker.get('row')!=row or marker.get('hours')!=payload['hours'] or marker.get('dates')!=payload['dates']:raise ValidationError('Já existe uma participação diferente para o funcionário '+row['number']+'. Não será substituída pela importação.')
            same.append(row['employee'])
        else:new.append(row['employee'])
    total=set(existing)|{r['employee'] for r in payload['rows']}|set(group.enrollments.filter(state='Activa').values_list('employee_id',flat=True))
    if len(total)>group.capacity:raise ValidationError('A capacidade do grupo não permite adicionar estes funcionários.')
    if Decimal(payload['hours'])!=group.hours_per_day:
        if existing or group.state!='Planeada':raise ValidationError('As horas do grupo já têm registos. Altere-as pelo processo de correcção antes de importar.')
        duration=Decimal((group.end_time.hour*60+group.end_time.minute)-(group.start_time.hour*60+group.start_time.minute))/60
        if Decimal(payload['hours'])+group.break_hours>duration:raise ValidationError('As horas do Excel ultrapassam o horário do grupo. Reveja o horário no Planeamento.')
    return new,same

def preview(user,group,payload):
    authorize(user);validate_group(group)
    if payload['group']!=group.pk or payload['course']!=group.course_id or payload['dates']!=[d.isoformat() for d in dates(group)]:raise ValidationError('O grupo mudou. Volte a importar o Excel.')
    new,same=validate_existing(group,payload)
    known=dict(Employee.objects.filter(pk__in=[r['employee'] for r in payload['rows']]).values_list('pk','name'))
    if len(known)!=len(payload['rows']):raise ValidationError('O cadastro mudou. Volte a importar.')
    items=[{'name':known[r['employee']],'number':r['number'],**row_facts(r,payload['hours'])} for r in payload['rows']]
    signed={'user':user.pk,'identity':identity(group),'digest':payload['digest']}
    return {'items':items,'new':len(new),'unchanged':len(same),'hours':payload['hours'],'certificates':sum(r['certificate'] is True for r in payload['rows']),'token':signing.dumps(signed,salt=SALT,compress=True)}

def apply(user,group_id,payload,token):
    authorize(user)
    try:signed=signing.loads(token,salt=SALT,max_age=900)
    except signing.BadSignature:raise ValidationError('A pré-visualização expirou. Volte a importar o Excel.')
    if signed['user']!=user.pk or signed['digest']!=payload['digest'] or digest({k:v for k,v in payload.items() if k not in ['digest','source_sha256']})!=payload['digest']:raise ValidationError('A pré-visualização não corresponde a esta importação.')
    with reservation_transaction():
        group=TrainingGroup.objects.select_related('course').get(pk=group_id)
        if payload['group']!=group.pk or signed['identity']['group']!=group.pk:raise ValidationError('Grupo inválido.')
        key='attendance-v1:'+payload['digest']
        previous=DocumentaryRecord.objects.filter(source_key=key,kind='attendance-import',state='applied').first()
        if previous:return {'created':0,'unchanged':len(payload['rows']),'already_applied':True,'record_id':previous.pk}
        if signed['identity']!=identity(group):raise ValidationError('O grupo mudou após a pré-visualização. Volte a importar.')
        new,same=validate_existing(group,payload);validate_group(group)
        official={p.pk:p for p in Employee.objects.filter(pk__in=[r['employee'] for r in payload['rows']])}
        if len(official)!=len(payload['rows']):raise ValidationError('Um funcionário deixou de pertencer ao cadastro oficial.')
        if Decimal(payload['hours'])!=group.hours_per_day:
            from .scheduling import save_group
            values={k:getattr(group,k) for k in ['start','end','provider','location','capacity','break_hours','start_time','end_time','centre','room','planning_confirmation']}
            values.update(dates=dates(group),hours_per_day=Decimal(payload['hours']),trainer=group.trainer_record or group.trainer)
            group=save_group(group.course_id,values,group_id=group.pk,revision=group.revision,actor=user.get_username())
        created=0
        for row in payload['rows']:
            if row['employee'] in same:continue
            facts=row_facts(row,payload['hours']);person=official[row['employee']]
            entry,was_new=Enrollment.objects.get_or_create(group=group,employee=person,defaults={'state':'Activa','confirmation':'Confirmado','reason':'Lista de assiduidade importada e confirmada.'})
            if entry.state!='Activa' or entry.training_id:raise ValidationError('A inscrição mudou. Volte a importar.')
            training=Training(employee=person,group=group,course=group.course,title=group.title,area=group.area,trainer=group.trainer,provider=group.provider,location=group.location,start=group.start,end=group.end,days=len(payload['dates']),hours_per_day=Decimal(payload['hours']),attendance=facts['presences'],attendance_confirmed=True,status='Concluído' if facts['presences'] else 'Por confirmar',certificate_issued=None,certificate_reference=row['reference'],source_key=f'clean-attendance:{group.pk}:{person.pk}',source_data={'_attendance_import_v1':{'row':row,'hours':payload['hours'],'dates':payload['dates'],'source_sha256':payload['source_sha256'],'logical_digest':payload['digest'],'certificate_states_version':2}})
            training.full_clean();training.save();entry.training=training;entry.save(update_fields=['training']);created+=1
        # Reuse the existing canonical projection and all existing calculations.
        from .interface_sync import reconcile
        reconcile(user.get_username())
        TrainingGroup.objects.filter(pk=group.pk,revision=group.revision).update(state='Concluída',revision=F('revision')+1)
        record=DocumentaryRecord.objects.create(source_key=key,kind='attendance-import',state='applied',payload=payload,payload_sha256=payload['digest'],package_sha256=payload['source_sha256'])
        AuditEvent.objects.create(actor=user.get_username(),action='ATTENDANCE_IMPORT_APPLIED',detail=json.dumps({'group_id':group.pk,'course_id':group.course_id,'created':created,'unchanged':len(same),'digest':payload['digest'],'certificates_obtained':sum(r['certificate'] is True for r in payload['rows']),'delivery_not_inferred':True},ensure_ascii=False))
        return {'created':created,'unchanged':len(same),'already_applied':False,'record_id':record.pk}

class UploadForm(forms.Form):
    file=forms.FileField(label='Excel de assiduidade',widget=forms.ClearableFileInput(attrs={'accept':'.xlsx','class':'gf-field','style':'width:100%;max-width:100%;'}))
    def clean_file(self):
        value=self.cleaned_data['file']
        if not value.name.lower().endswith('.xlsx') or value.size>MAX_BYTES:raise ValidationError('Seleccione um ficheiro .xlsx até 5 MB.')
        return value

@login_required
@permission_required(PERMISSIONS,raise_exception=True)
@require_http_methods(['GET','POST'])
def review(request,pk=None):
    authorize(request.user);request.group_validation_managed=True
    if pk is None:
        return render(request,'planning/attendance_import.html',{'groups':TrainingGroup.objects.select_related('course').exclude(state='Cancelada').order_by('-start','pk')[:200]})
    group=get_object_or_404(TrainingGroup.objects.select_related('course'),pk=pk)
    uploading=request.method=='POST' and request.POST.get('action')=='preview'
    form=UploadForm(request.POST if uploading else None,request.FILES if uploading else None);proposal=None;error='';success=None;status=200;key='attendance_preview_'+str(pk)
    try:
        validate_group(group)
        if request.method=='POST' and request.POST.get('action')=='preview' and form.is_valid():
            payload=parse_file(form.cleaned_data['file'].read(),group);proposal=preview(request.user,group,payload);request.session[key]=payload
        elif request.method=='POST' and request.POST.get('action')=='confirm':
            if request.POST.get('confirm')!='on':raise ValidationError('Confirme os resultados antes de gravar.')
            payload=request.session.get(key)
            if not payload:raise ValidationError('Importe o Excel para visualizar os resultados primeiro.')
            success=apply(request.user,pk,payload,request.POST.get('token',''))
    except (ValidationError,IntegrityError,OperationalError) as exc:
        error=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Não foi possível gravar. Volte a importar para verificar os dados.';status=409 if request.method=='POST' else 200
    return render(request,'planning/attendance_import.html',{'group':group,'form':form,'proposal':proposal,'error':error,'success':success},status=status)

@login_required
@permission_required(PERMISSIONS,raise_exception=True)
@require_http_methods(['GET'])
def download(request,pk):
    authorize(request.user);group=get_object_or_404(TrainingGroup.objects.select_related('course'),pk=pk);validate_group(group)
    from .attendance_template import make_workbook
    return FileResponse(io.BytesIO(make_workbook(group)),as_attachment=True,filename=f'PMFE_Assiduidade_{group.course_id}_{group.code}.xlsx',content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

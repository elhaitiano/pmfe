from datetime import date,time
from decimal import Decimal
import io,json,tempfile,hashlib
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from zipfile import ZipFile
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings, Client
from django.urls import reverse
from .models import Course,Employee,Training,TrainingGroup,GroupSession,Enrollment,AuditEvent,PlanningDraft,DraftParticipant,EligibilityDecision
from .planning import create_group,history_rows,enrol_reviewed
from .eligibility import assess,require_admission,validate_export
from . import draft_planning as service


@override_settings(PMF_SIMPLIFIED_PLANNING_ENABLED=True,PMF_HISTORICAL_ENABLED=False)
class PlanningWorkflowTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.override=override_settings(DATA_DIR=Path(self.temp.name),PMF_HISTORICAL_DATA_FILE=None,PMF_ACHIEVEMENT_DATA_FILE=None)
        self.override.enable();self.addCleanup(self.override.disable)
        self.admin=get_user_model().objects.create_superuser('workflow-admin','test@example.invalid',password=None)
        self.client.force_login(self.admin)
        self.course=Course.objects.create(title='Curso sintético A',area='Técnica',validation_status='validated',validation_notes='Decisão fictícia para testes')
        self.people=[Employee.objects.create(number=f'TEST-W-{i}',name=f'Pessoa sintética {i}') for i in range(3)]
        self.values={'dates':[date(2026,1,5),date(2026,1,7)],'trainer':'Formador sintético','provider':'Teste','location':'Sala de teste',
            'capacity':20,'hours_per_day':Decimal('7'),'break_hours':Decimal('1'),'start_time':time(8),'end_time':time(16)}
        self.raw=service.serialize(self.values)

    def draft(self,course=True):
        return PlanningDraft.objects.create(owner=self.admin,course=self.course if course else None,title=self.course.title if course else 'Nova acção sintética',group_data=self.raw)

    def member(self,draft,person=None):
        return DraftParticipant.objects.create(draft=draft,employee=person or self.people[0])

    def add(self,group,people,choice='add',reason=''):
        group.refresh_from_db();checked=history_rows(group,people)
        return enrol_reviewed(group,people,{str(p.pk):(choice,reason) for p in people},
            {str(x['person'].pk):x['key'] for x in checked},group.revision,actor=self.admin)

    def prior(self,person=None,**kw):
        data={'employee':person or self.people[0],'title':self.course.title,'start':date(2025,1,1),'end':date(2025,1,2),
            'days':2,'hours_per_day':7,'attendance':2,'attendance_confirmed':True,'status':'Concluído'}
        data.update(kw);return Training.objects.create(**data)

    def approve(self,item,*,group=None,draft=None):
        return EligibilityDecision.objects.create(course=self.course,employee=item['person'],draft=draft,group=group,
            kind=item['decision_kind'],basis=item['basis'],reason='Reciclagem autorizada para testes',reference='TEST-DOC',actor=self.admin)

    def publish(self,draft):
        checked=service.entries(draft)
        return service.publish(self.admin,draft,draft.revision,{str(x['person'].pk):x['key'] for x in checked})

    def test_existing_course_draft_publication_and_numbering(self):
        draft=self.draft();self.member(draft)
        self.assertEqual((TrainingGroup.objects.count(),Training.objects.count()),(0,0))
        group=self.publish(draft)
        self.assertEqual(group.code,'G1');self.assertEqual(group.sessions.count(),2)
        self.assertEqual(group.enrollments.count(),1);self.assertEqual(Training.objects.count(),0)
        draft.refresh_from_db();self.assertEqual(draft.state,'published')
        with self.assertRaises(ValidationError):self.publish(draft)
        self.assertEqual(TrainingGroup.objects.count(),1)
        second=self.draft();self.member(second,self.people[1])
        self.assertEqual(self.publish(second).code,'G2')

    def test_new_action_remains_pending_and_has_no_automatic_duration_or_approval(self):
        draft=self.draft(False);self.member(draft)
        with self.assertRaises(ValidationError):self.publish(draft)
        self.assertEqual(Course.objects.count(),1)
        new=service.register_course(self.admin,draft,draft.revision)
        self.assertEqual(new.validation_status,'pending');self.assertIsNone(new.default_hours)
        draft.refresh_from_db()
        with self.assertRaises(ValidationError):self.publish(draft)
        new.validation_status='validated';new.validation_notes='Decisão fictícia explícita';new.save()
        draft.refresh_from_db()
        self.assertEqual(self.publish(draft).code,'G1')

    def test_incomplete_drafts_can_be_saved_resumed_and_reject_invalid_numbers(self):
        form=service.DraftForm({'title':'Preparação incompleta','revision':0},mode='new')
        self.assertTrue(form.is_valid(),form.errors)
        draft=service.save(self.admin,form)
        self.assertEqual(draft.group_data['dates'],'')
        self.assertEqual(TrainingGroup.objects.count(),0)
        with self.assertRaises(ValidationError):service.validate(draft)
        for value in ('-2','0','NaN','Infinity'):
            form=service.DraftForm({'title':'Preparação','revision':0,'hours_per_day':value},mode='new')
            self.assertFalse(form.is_valid())

    def test_stale_draft_revision_cannot_overwrite_saved_values(self):
        draft=self.draft()
        raw={**self.raw,'course':self.course.pk,'revision':draft.revision}
        form=service.DraftForm(raw);self.assertTrue(form.is_valid(),form.errors)
        service.save(self.admin,form,draft)
        with self.assertRaises(ValidationError):service.save(self.admin,form,draft)

    def test_unbound_exact_title_is_uncertain_and_similar_titles_not_identical(self):
        self.prior()
        group=create_group(self.course,self.values)
        item=assess(self.course,self.people[:1],group=group)[0]
        self.assertEqual(item['state'],'review')
        with self.assertRaises(ValidationError):self.add(group,self.people[:1],choice='repeat',reason='Motivo isolado não é autorização')
        self.approve(item,group=group)
        self.add(group,self.people[:1])
        self.assertEqual(group.enrollments.count(),1)
        other=Course.objects.create(title='Curso sintético A avançado',validation_status='validated',validation_notes='Teste')
        self.assertEqual(assess(other,self.people[:1])[0]['state'],'eligible')

    def test_linked_documented_completed_participation_requires_specific_decision(self):
        old=create_group(self.course,self.values)
        from .scheduling_guard import reservation_transaction
        with reservation_transaction():
            old.state='Concluída';old.save()
        self.prior(group=old)
        group=create_group(self.course,self.values)
        item=assess(self.course,self.people[:1],group=group)[0]
        self.assertEqual(item['state'],'repeat')
        with self.assertRaises(ValidationError):self.add(group,self.people[:1])
        self.approve(item,group=group)
        self.add(group,self.people[:1])
        self.assertTrue(AuditEvent.objects.filter(action='PLANNING_ENROL_SECURE').exists())

    def test_confirmed_absence_allows_new_enrolment_but_positive_unconfirmed_presence_does_not(self):
        old=create_group(self.course,self.values)
        self.prior(group=old,attendance=0)
        group=create_group(self.course,self.values)
        self.assertEqual(assess(self.course,self.people[:1],group=group)[0]['state'],'absent')
        self.add(group,self.people[:1])
        self.prior(person=self.people[1],group=old,attendance_confirmed=False)
        self.assertEqual(assess(self.course,self.people[1:2],group=group)[0]['state'],'review')

    def test_active_group_cannot_be_bypassed_with_repeat_but_transfer_is_atomic(self):
        origin=create_group(self.course,self.values);self.add(origin,self.people[:1])
        target=create_group(self.course,self.values)
        with self.assertRaises(ValidationError):self.add(target,self.people[:1],choice='repeat',reason='Bypass')
        self.add(target,self.people[:1],choice='transfer',reason='Transferência de teste')
        self.assertEqual(origin.enrollments.get().state,'Transferida')
        self.assertEqual(target.enrollments.count(),1)
        with self.assertRaises(ValidationError):self.add(target,self.people[:1])

    def test_review_stales_when_another_group_accepts_the_same_employee(self):
        a=create_group(self.course,self.values);b=create_group(self.course,self.values)
        keys={str(x['person'].pk):x['key'] for x in assess(self.course,self.people[:1],group=b)}
        self.add(a,self.people[:1])
        with self.assertRaises(ValidationError):enrol_reviewed(b,self.people[:1],{str(self.people[0].pk):('add','')},keys,b.revision,actor=self.admin)
        self.assertEqual(b.enrollments.count(),0)

    def test_excel_rechecks_history_and_does_not_invent_attendance(self):
        group=create_group(self.course,self.values);self.add(group,self.people[:1])
        url=reverse('attendance_excel',args=[group.pk])
        response=self.client.get(url,{'date':'2026-01-05'});self.assertEqual(response.status_code,200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'PK'));response.close()
        self.prior()
        response=self.client.get(url,{'date':'2026-01-05'})
        self.assertEqual(response.status_code,400);self.assertContains(response,'Lista não gerada',status_code=400)
        self.assertEqual(Training.objects.count(),1)

    def test_preparation_xlsx_preserves_template_parts_and_marks_every_page(self):
        draft=self.draft(False)
        for i in range(13):self.member(draft,Employee.objects.create(number=f'XLS-TEST-{i}',name=f'Participante teste {i:02}'))
        from .attendance_excel import TEMPLATE,NS
        import xml.etree.ElementTree as ET
        data=service.preparation_excel(draft,date(2026,1,5))
        with ZipFile(io.BytesIO(data)) as workbook,ZipFile(TEMPLATE) as source:
            for part in ('xl/styles.xml','xl/media/image1.png','xl/media/image2.png'):
                self.assertEqual(workbook.read(part),source.read(part))
            strings=ET.fromstring(workbook.read('xl/sharedStrings.xml'))
            sheet=ET.fromstring(workbook.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(' '.join(sheet.itertext()).count('PREPARAÇÃO — RASCUNHO'),2)
            self.assertEqual(len(sheet.findall('s:sheetData/s:row',NS)),48)
        self.assertEqual((Training.objects.count(),TrainingGroup.objects.count()),(0,0))

    def test_publication_failure_rolls_back_group_number_inscriptions_and_audit(self):
        draft=self.draft();self.member(draft);before=AuditEvent.objects.count()
        with patch('training.draft_planning.enrol_reviewed',side_effect=ValidationError('Injected failure')):
            with self.assertRaises(ValidationError):self.publish(draft)
        self.course.refresh_from_db();draft.refresh_from_db()
        self.assertEqual(self.course.next_group,1);self.assertEqual(draft.revision,1)
        self.assertEqual((TrainingGroup.objects.count(),Enrollment.objects.count(),AuditEvent.objects.count()),(0,0,before))

    def test_draft_decision_moves_to_group_without_altering_historical_row(self):
        historical=self.prior();original=historical.source_data.copy()
        draft=self.draft();self.member(draft)
        item=service.entries(draft)[0];approval=self.approve(item,draft=draft)
        group=self.publish(draft)
        approval.refresh_from_db();self.assertEqual(approval.group_id,group.pk)
        historical.refresh_from_db();self.assertEqual(historical.source_data,original)
        validate_export(group,list(group.enrollments.select_related('employee')))

    def test_feature_flag_and_permissions_gate_all_new_routes(self):
        with override_settings(PMF_SIMPLIFIED_PLANNING_ENABLED=False):
            self.assertEqual(self.client.get(reverse('planning_drafts')).status_code,404)
        reader=get_user_model().objects.create_user('reader-without-permission',is_staff=True,password=None)
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse('planning_drafts')).status_code,403)
        self.assertEqual(self.client.get(reverse('planning_draft_create')).status_code,403)
        self.client.logout();self.assertEqual(self.client.get(reverse('planning_drafts')).status_code,302)

    def test_ui_new_and_existing_journeys_work_without_javascript(self):
        url=reverse('planning_draft_create')+'?mode=new'
        response=self.client.post(url,{'title':'Nova preparação UI','revision':0})
        self.assertEqual(response.status_code,302)
        draft=PlanningDraft.objects.get(title='Nova preparação UI')
        self.assertContains(self.client.get(response.url),'Rascunho — sem aprovação formal')
        url=reverse('planning_draft_create')+'?mode=existing'
        response=self.client.post(url,{**self.raw,'course':self.course.pk,'revision':0})
        self.assertEqual(response.status_code,302)
        draft=PlanningDraft.objects.get(course=self.course)
        response=self.client.post(reverse('planning_draft_people',args=[draft.pk]),{'action':'add','revision':draft.revision,'employees':[self.people[0].pk]})
        self.assertEqual(response.status_code,302)
        draft.refresh_from_db()
        page=self.client.post(reverse('planning_draft_edit',args=[draft.pk]),{'action':'review'})
        self.assertEqual(page.status_code,200);self.assertTrue(page.context['token'])
        response=self.client.post(reverse('planning_draft_edit',args=[draft.pk]),{'action':'publish','confirm':'yes','token':page.context['token']})
        self.assertEqual(response.status_code,302);self.assertEqual(TrainingGroup.objects.count(),1)

    def test_documented_source_binding_is_used_instead_of_title_similarity(self):
        from .historical import signature
        from django.db.models import TextField
        from django.db.models.functions import Cast
        self.course.source_system='epal-proagua-catalogo';self.course.source_action_id='TEST-ACTION';self.course.save()
        previous=self.prior(title='Designação documental distinta',source_key='TEST-SOURCE-1')
        previous=Training.objects.annotate(source_json_text=Cast('source_data',TextField())).get(pk=previous.pk)
        manifest={'bindings':{previous.source_key:{'level':'confirmed','action_id':'TEST-ACTION','edition':'TEST-ED',
            'signature':signature(previous),'employee_number_sha256':hashlib.sha256(previous.employee.number.encode()).hexdigest()}},
            'editions':{'TEST-ED':{'display_id':'Edição documental de teste'}}}
        with override_settings(PMF_HISTORICAL_ENABLED=True),patch('training.historical.manifest',return_value=manifest):
            self.assertEqual(assess(self.course,self.people[:1])[0]['state'],'repeat')
            manifest['bindings'][previous.source_key]['signature']='invalid'
            self.assertEqual(assess(self.course,self.people[:1])[0]['state'],'review')

    def test_certificate_is_not_proof_of_participation(self):
        old=create_group(self.course,self.values)
        self.prior(group=old,attendance=0,attendance_confirmed=False,certified=True)
        self.assertEqual(assess(self.course,self.people[:1])[0]['state'],'review')

    def test_specific_authorization_token_cannot_be_replayed_or_forged(self):
        self.prior();group=create_group(self.course,self.values)
        url=reverse('eligibility_group_decision',args=[group.pk,self.people[0].pk])
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        token=response.context['token']
        data={'token':token,'reason':'Reciclagem autorizada em teste','reference':'TEST-REF','confirm':'on'}
        forged=self.client.post(url,{**data,'token':token+'bad'})
        self.assertEqual(forged.status_code,200);self.assertEqual(EligibilityDecision.objects.count(),0)
        response=self.client.post(url,data);self.assertEqual(response.status_code,302)
        self.assertEqual(EligibilityDecision.objects.count(),1)
        self.client.post(url,data);self.assertEqual(EligibilityDecision.objects.count(),1)
        self.add(group,self.people[:1])
        self.assertEqual(Training.objects.count(),1)

    def test_authorization_fails_after_history_change_and_for_other_target(self):
        prior=self.prior();group=create_group(self.course,self.values)
        item=assess(self.course,self.people[:1],group=group)[0];self.approve(item,group=group)
        other=create_group(self.course,self.values)
        self.assertIsNone(assess(self.course,self.people[:1],group=other)[0]['authorization'])
        Training.objects.filter(pk=prior.pk).update(attendance=1)
        with self.assertRaises(ValidationError):self.add(group,self.people[:1])

    def test_publication_signature_confirmation_and_csrf(self):
        draft=self.draft();self.member(draft)
        url=reverse('planning_draft_edit',args=[draft.pk])
        response=self.client.post(url,{'action':'review'});token=response.context['token']
        self.client.post(url,{'action':'publish','token':token,'confirm':'no'})
        self.assertEqual(TrainingGroup.objects.count(),0)
        self.client.post(url,{'action':'publish','token':token+'bad','confirm':'yes'})
        self.assertEqual(TrainingGroup.objects.count(),0)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.admin)
        self.assertEqual(strict.post(url,{'action':'publish','token':token,'confirm':'yes'}).status_code,403)

    def test_draft_transfer_is_reviewed_then_moves_enrolment_on_publication(self):
        old=create_group(self.course,self.values);self.add(old,self.people[:1])
        draft=self.draft();member=self.member(draft);member.transfer_requested=True;member.reason='Transferência autorizada';member.save()
        with self.assertRaises(ValidationError):service.preparation_excel(draft,date(2026,1,5))
        group=self.publish(draft)
        self.assertEqual(old.enrollments.get().state,'Transferida')
        self.assertEqual(group.enrollments.get().employee_id,self.people[0].pk)

    def test_failure_in_audit_rolls_back_enrolment_and_revision(self):
        group=create_group(self.course,self.values);revision=group.revision
        with patch('training.models.AuditEvent.objects.create',side_effect=RuntimeError('Injected audit failure')):
            with self.assertRaises(RuntimeError):self.add(group,self.people[:1])
        group.refresh_from_db();self.assertEqual(group.revision,revision)
        self.assertEqual(Enrollment.objects.count(),0)

    def test_calendar_change_invalidates_the_specific_authorization(self):
        self.prior();group=create_group(self.course,self.values)
        self.approve(assess(self.course,self.people[:1],group=group)[0],group=group)
        TrainingGroup.objects.filter(pk=group.pk).update(location='Outra sala de teste')
        group.refresh_from_db()
        with self.assertRaises(ValidationError):self.add(group,self.people[:1])

    def test_drafts_and_publication_preserve_historical_records_and_official_statistics(self):
        from .analytics import statistics
        historical=self.prior(person=self.people[2],title='Outra formação sintética')
        baseline=list(Training.objects.values())
        employees=list(Employee.objects.values())
        before=statistics(list(Training.objects.all()))['total']
        draft=self.draft();self.member(draft)
        self.publish(draft)
        self.assertEqual(list(Training.objects.values()),baseline)
        self.assertEqual(list(Employee.objects.values()),employees)
        self.assertEqual(statistics(list(Training.objects.all()))['total'],before)
        self.assertEqual(Training.objects.get(pk=historical.pk).attendance,2)

    def test_direct_decision_and_export_need_their_own_permissions(self):
        self.prior();draft=self.draft();self.member(draft)
        reader=get_user_model().objects.create_user('workflow-unprivileged',is_staff=True,password=None)
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse('eligibility_draft_decision',args=[draft.pk,self.people[0].pk])).status_code,403)
        self.assertEqual(self.client.get(reverse('planning_draft_excel',args=[draft.pk]),{'date':'2026-01-05'}).status_code,403)
        self.assertEqual(EligibilityDecision.objects.count(),0)

    def test_expired_or_other_user_publication_token_is_rejected(self):
        import time as clock
        draft=self.draft();self.member(draft)
        url=reverse('planning_draft_edit',args=[draft.pk])
        token=self.client.post(url,{'action':'review'}).context['token']
        other=get_user_model().objects.create_superuser('other-synthetic','other@example.invalid',password=None)
        self.client.force_login(other)
        self.client.post(url,{'action':'publish','confirm':'yes','token':token})
        self.assertEqual(TrainingGroup.objects.count(),0)
        self.client.force_login(self.admin)
        future=clock.time()+1800
        with patch('django.core.signing.time.time',return_value=future):
            self.client.post(url,{'action':'publish','confirm':'yes','token':token})
        self.assertEqual(TrainingGroup.objects.count(),0)


@override_settings(PMF_SIMPLIFIED_PLANNING_ENABLED=True,PMF_HISTORICAL_ENABLED=False)
class ParallelEligibilityTests(TransactionTestCase):
    def test_parallel_enrolment_in_two_groups_never_duplicates_active_registration(self):
        from .models import PlanningLock
        PlanningLock.objects.get_or_create(pk=1)
        import threading,queue
        from django.db import connections,OperationalError
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        with override_settings(DATA_DIR=Path(temp.name),PMF_HISTORICAL_DATA_FILE=None,PMF_ACHIEVEMENT_DATA_FILE=None):
            user=get_user_model().objects.create_superuser('parallel-synthetic','parallel@example.invalid',password=None)
            course=Course.objects.create(title='Concorrência sintética',validation_status='validated',validation_notes='Teste')
            person=Employee.objects.create(number='TEST-PARALLEL',name='Pessoa sintética paralela')
            values={'dates':[date(2026,1,1)],'trainer':'Teste','provider':'Teste','location':'Teste','capacity':5,
                'hours_per_day':Decimal(7),'break_hours':Decimal(1),'start_time':time(8),'end_time':time(16)}
            groups=[create_group(course,values),create_group(course,values)]
            keys=[{str(x['person'].pk):x['key'] for x in assess(course,[person],group=g)} for g in groups]
            barrier=threading.Barrier(2);results=queue.Queue()
            def worker(index):
                try:
                    connections['default'].ensure_connection()
                    barrier.wait(timeout=5)
                    enrol_reviewed(groups[index],[person],{str(person.pk):('add','')},keys[index],1,actor=user)
                    results.put('accepted')
                except (ValidationError,OperationalError):results.put('refused')
                except Exception as exc:results.put(type(exc).__name__+':'+str(exc))
                finally:connections['default'].close()
            workers=[threading.Thread(target=worker,args=(i,),daemon=True) for i in range(2)]
            for thread in workers:thread.start()
            for thread in workers:thread.join(timeout=10)
            self.assertFalse(any(t.is_alive() for t in workers),'A concurrent worker did not finish')
            outcomes=[results.get_nowait(),results.get_nowait()]
            self.assertEqual(sorted(outcomes),['accepted','refused'])
            self.assertEqual(Enrollment.objects.filter(employee=person,state='Activa').count(),1)

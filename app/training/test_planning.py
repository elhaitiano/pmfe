from datetime import date, time
from decimal import Decimal
import io
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from .models import Course, TrainingGroup, Enrollment, Employee, Training
from .planning import create_group, history_rows, enrol_reviewed, GroupForm
from .batch import edition_queryset
from .dashboard import executive_data


class PlanningTests(TestCase):
    def setUp(self):
        self.staff=get_user_model().objects.create_user('planning-admin',password='qa',is_staff=True)
        self.reader=get_user_model().objects.create_user('planning-reader',password='qa')
        self.client.force_login(self.staff)
        self.course=Course.objects.create(validation_status='validated', validation_notes='Synthetic approved fixture', title='Liderança e Gestão',default_days=2)
        self.people=[Employee.objects.create(number=str(100+i),name=f'Formando {i}',direction='Direcção A',email=f'a{i}@example.org') for i in range(3)]
        self.values=dict(dates=[date(2026,1,1),date(2026,1,3)],trainer='Formador A',provider='Entidade',location='Kikuxi',capacity=30,hours_per_day=Decimal('7'),break_hours=Decimal('1'),start_time=time(8),end_time=time(16))
        self.group=create_group(self.course,self.values)

    def add(self,group,people,choice='add',reason=''):
        group.refresh_from_db();items=history_rows(group,people)
        keys={str(x['person'].pk):x['key'] for x in items}
        return enrol_reviewed(group,people,{str(p.pk):(choice,reason) for p in people},keys,group.revision)

    def start(self,group):
        group.refresh_from_db()
        return self.client.post(reverse('group_transition',args=[group.pk]),{'revision':group.revision,'action':'start'})

    def prior(self,person,**kw):
        values=dict(employee=person,title=self.course.title,area='Técnica',trainer='Antigo',location='ETA',start=date(2025,1,1),end=date(2025,1,2),days=2,hours_per_day=7,attendance=0,status='Concluído')
        values.update(kw);return Training.objects.create(**values)

    def test_group_numbers_are_per_course_and_cancelled_not_reused(self):
        self.client.post(reverse('group_transition',args=[self.group.pk]),{'revision':1,'action':'cancel','reason':'Adiada'})
        self.assertEqual(create_group(self.course,self.values).code,'G2')
        other=Course.objects.create(validation_status='validated', validation_notes='Synthetic approved fixture', title='Outra formação')
        self.assertEqual(create_group(other,self.values).code,'G1')
        self.assertEqual(self.group.sessions.count(),2)
        response=self.client.post(reverse('course_create'),dict(title='  LIDERANÇA   e Gestão ',area='',contents='',default_days=1,default_hours=7,active='on'))
        self.assertEqual(response.status_code,200);self.assertEqual(Course.objects.count(),2)

    def test_calendar_checks_duplicate_days_and_hours(self):
        data={**self.values,'dates':'2026-01-01\n2026-01-01'}
        self.assertFalse(GroupForm(data).is_valid())
        data['dates']='2026-01-01\n2026-01-03';data['end_time']='12:00'
        self.assertFalse(GroupForm(data).is_valid());data['end_time']='16:00'
        self.assertTrue(GroupForm(data).is_valid())

    def test_enrolments_do_not_invent_attendance_or_training_history(self):
        self.add(self.group,self.people[:2]);self.assertEqual(Training.objects.count(),0)
        self.assertEqual(Enrollment.objects.filter(state='Activa').count(),2)
        with self.assertRaises(ValidationError):self.add(self.group,self.people[:1])
        self.start(self.group);self.assertEqual(Training.objects.count(),2)
        for t in Training.objects.all():
            self.assertEqual(t.status,'Em Curso');self.assertEqual(t.attendance,0);self.assertFalse(t.attendance_confirmed);self.assertIsNone(t.grade);self.assertIsNone(t.certified)
        self.group.refresh_from_db();self.assertEqual(self.group.state,'Em Curso')
        before=Training.objects.count();self.start(self.group);self.assertEqual(Training.objects.count(),before)

    def test_history_distinguishes_presence_confirmed_absence_and_unknown(self):
        self.prior(self.people[0],attendance=1)
        self.prior(self.people[1],attendance_confirmed=True)
        self.prior(self.people[2])
        entries=history_rows(self.group,self.people)
        self.assertEqual([e['warnings'][0]['kind'] for e in entries],['Participação anterior','Ausência confirmada','Participação por confirmar'])
        with self.assertRaises(ValidationError):self.add(self.group,self.people[:1])
        with self.assertRaises(ValidationError):self.add(self.group,self.people[:1],choice='repeat')
        self.add(self.group,self.people[:1],choice='repeat',reason='Actualização de competências')
        self.add(self.group,self.people[1:2]);self.assertEqual(Training.objects.count(),3)

    def test_transfer_multiple_people_preserves_origin_and_versions(self):
        self.add(self.group,self.people[:2]);target=create_group(self.course,self.values)
        self.add(target,self.people[:2],choice='transfer',reason='Alteração de disponibilidade')
        self.assertEqual(self.group.enrollments.filter(state='Transferida').count(),2)
        self.assertEqual(target.enrollments.filter(state='Activa').count(),2)
        self.assertEqual(Training.objects.count(),0)
        with self.assertRaises(ValidationError):self.add(self.group,self.people[:1])

    def test_transfer_after_start_is_blocked_and_history_retained(self):
        self.add(self.group,self.people[:1]);self.start(self.group)
        target=create_group(self.course,self.values)
        with self.assertRaises(ValidationError):self.add(target,self.people[:1],choice='transfer',reason='Mudar grupo')
        self.assertEqual(Enrollment.objects.filter(state='Activa').count(),1)
        self.add(target,self.people[:1],choice='repeat',reason='Participação anterior por confirmar')
        self.assertEqual(Training.objects.count(),1)

    def test_capacity_and_stale_review_rollback_entire_selection(self):
        TrainingGroup.objects.filter(pk=self.group.pk).update(capacity=1);self.group.refresh_from_db()
        rev=self.group.revision
        with self.assertRaises(ValidationError):self.add(self.group,self.people[:2])
        self.group.refresh_from_db();self.assertEqual(self.group.revision,rev);self.assertFalse(Enrollment.objects.exists())
        items=history_rows(self.group,self.people[:1]);keys={str(items[0]['person'].pk):items[0]['key']}
        self.prior(self.people[0],attendance=1)
        with self.assertRaises(ValidationError):enrol_reviewed(self.group,self.people[:1],{str(self.people[0].pk):('add','')},keys,rev)
        self.assertFalse(Enrollment.objects.exists())

    def test_signed_review_requires_exact_selection_and_reason(self):
        self.prior(self.people[0],attendance=1);url=reverse('group_enrol',args=[self.group.pk])
        review=self.client.post(url,{'action':'review','employees':[self.people[0].pk]})
        self.assertContains(review,'Já participou');self.assertFalse(Enrollment.objects.exists())
        data={'action':'confirm','employees':[self.people[0].pk],'review_token':review.context['review_token'],f'choice_{self.people[0].pk}':'repeat',f'reason_{self.people[0].pk}':'Reciclagem'}
        result=self.client.post(url,data);self.assertEqual(result.status_code,302);self.assertEqual(Enrollment.objects.count(),1)
        self.assertEqual(self.client.post(url,data).status_code,200);self.assertEqual(Enrollment.objects.count(),1)

    def test_parallel_groups_with_identical_dates_stay_separate(self):
        self.add(self.group,self.people[:1]);self.start(self.group)
        target=create_group(self.course,self.values);self.add(target,self.people[1:2]);self.start(target)
        seed=self.group.trainings.first();self.assertEqual(edition_queryset(seed).count(),1)
        stats=executive_data(Training.objects.select_related('employee'),Employee.objects.all())
        self.assertEqual(stats['kpis'][2]['value'],2)
        form=self.client.get(reverse('training_edit',args=[seed.pk])).context['form']
        self.assertTrue(form.fields['title'].disabled)
        response=self.client.post(reverse('training_delete',args=[seed.pk]));self.assertEqual(response.status_code,302);self.assertEqual(Training.objects.count(),2)

    def test_completion_requires_verified_attendance_and_results(self):
        self.add(self.group,self.people[:1]);self.start(self.group)
        self.group.refresh_from_db();url=reverse('group_transition',args=[self.group.pk])
        self.client.post(url,{'action':'finish','revision':self.group.revision});self.group.refresh_from_db();self.assertEqual(self.group.state,'Em Curso')
        t=self.group.trainings.first();t.status='Concluído';t.attendance_confirmed=True;t.save()
        self.client.post(url,{'action':'finish','revision':self.group.revision});self.group.refresh_from_db();self.assertEqual(self.group.state,'Concluída')

    def test_pdf_only_scheduled_day_full_names_blank_signatures_no_contact_notes(self):
        self.add(self.group,self.people);en=self.group.enrollments.first();en.contact_note='CONTACT_NOTE_PRIVATE';en.save()
        url=reverse('attendance_pdf',args=[self.group.pk]);response=self.client.get(url,{'date':'2026-01-01'})
        self.assertEqual(response.status_code,200);binary=b''.join(response.streaming_content);self.assertTrue(binary.startswith(b'%PDF'))
        try:from pypdf import PdfReader
        except ImportError:return
        reader=PdfReader(io.BytesIO(binary));text=' '.join(p.extract_text() for p in reader.pages)
        self.assertIn('Formando 0',text);self.assertIn('Hora chegada',' '.join(text.split()));self.assertIn('Rubrica',text);self.assertIn('Pág. 1/1',text);self.assertNotIn('CONTACT_NOTE_PRIVATE',text)
        self.assertEqual(self.client.get(url,{'date':'2026-01-02'}).status_code,400)

    def test_permissions_csrf_and_read_only_gets(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(reverse('planning')).status_code,200)
        for name,args in [('course_create',[]),('group_enrol',[self.group.pk]),('group_create',[self.course.pk])]:self.assertEqual(self.client.get(reverse(name,args=args)).status_code,403)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.staff)
        self.assertEqual(strict.post(reverse('group_transition',args=[self.group.pk]),{'action':'start','revision':1}).status_code,403)
        self.client.force_login(self.staff);self.assertEqual(self.client.get(reverse('group_transition',args=[self.group.pk])).status_code,405)

class GroupDeletionTests(TestCase):
    setUp=PlanningTests.setUp
    add=PlanningTests.add
    start=PlanningTests.start

    def deletion(self, **kw):
        self.group.refresh_from_db()
        data={'revision':self.group.revision,'confirm':'ELIMINAR','reason':'Turma de teste'}
        data.update(kw)
        return self.client.post(reverse('group_delete',args=[self.group.pk]),data)

    def test_delete_empty_group_and_never_reuse_number(self):
        old_pk=self.group.pk
        self.assertEqual(self.deletion().status_code,302)
        self.assertFalse(TrainingGroup.objects.filter(pk=old_pk).exists())
        new=create_group(self.course,self.values)
        self.assertEqual(new.number,2)
        self.assertEqual(Employee.objects.count(),3)

    def test_delete_preparatory_enrollments_and_sessions_only(self):
        from .models import GroupSession, AuditEvent
        self.add(self.group,self.people)
        other=create_group(self.course,self.values)
        self.deletion()
        self.assertEqual(Enrollment.objects.count(),0)
        self.assertEqual(GroupSession.objects.filter(group=other).count(),2)
        self.assertEqual(Employee.objects.count(),3)
        self.assertTrue(AuditEvent.objects.filter(action='PLANNING_DELETE').exists())

    def test_started_group_is_blocked(self):
        self.add(self.group,self.people[:1]);self.start(self.group)
        self.deletion()
        self.assertTrue(TrainingGroup.objects.filter(pk=self.group.pk).exists())
        self.assertEqual(Training.objects.count(),1)

    def test_reader_and_get_do_not_delete(self):
        url=reverse('group_delete',args=[self.group.pk])
        self.assertEqual(self.client.get(url).status_code,200)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.deletion().status_code,403)
        self.assertTrue(TrainingGroup.objects.filter(pk=self.group.pk).exists())

    def test_revision_and_explicit_confirmation_required(self):
        for kw in [{'revision':0},{'confirm':'sim'},{'reason':''}]:
            self.deletion(**kw)
            self.assertTrue(TrainingGroup.objects.filter(pk=self.group.pk).exists())

    def test_cancelled_group_can_be_deleted(self):
        self.client.post(reverse('group_transition',args=[self.group.pk]),{'revision':self.group.revision,'action':'cancel','reason':'Teste'})
        self.deletion()
        self.assertFalse(TrainingGroup.objects.filter(pk=self.group.pk).exists())

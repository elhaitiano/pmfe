from datetime import date,time
from decimal import Decimal
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from .models import Course,Employee,Training,TrainingGroup,Enrollment,AuditEvent
from .scheduling import save_group
from .planning import history_rows,enrol_reviewed,course_deletion_allowed
from .catalogue_actions import creation_reason

@override_settings(PMF_SIMPLIFIED_PLANNING_ENABLED=True,PMF_HISTORICAL_ENABLED=False)
class PracticalCatalogueTests(TestCase):
 def setUp(self):
  self.user=get_user_model().objects.create_superuser('catalogue-test','test@example.invalid','test-only')
  self.client.force_login(self.user)
  self.course=Course.objects.create(title='Formacao de ensaio',validation_status='pending',default_days=1,default_hours=7)
  self.person=Employee.objects.create(number='TEST-CAT',name='Pessoa ficticia')
 def values(self,day=20,capacity=2):
  return dict(dates=[date(2026,11,day)],start=date(2026,11,day),end=date(2026,11,day),trainer='Formador de ensaio',provider='EPAL',location='Local de ensaio',capacity=capacity,hours_per_day=Decimal(7),break_hours=Decimal(1),start_time=time(8),end_time=time(16))
 def group(self,day=20):return save_group(self.course.pk,self.values(day),actor=self.user.username)
 def enrol(self,group,person):
  group.refresh_from_db();rows=history_rows(group,[person]);return enrol_reviewed(group,[person],{str(person.pk):('add','')},{str(person.pk):rows[0]['key']},group.revision,actor=self.user)
 def test_pending_documentation_does_not_block_creation(self):
  self.assertTrue(self.course.can_create_groups);self.assertEqual(creation_reason(self.course,self.user),'');g=self.group();self.assertEqual(g.number,1);self.course.refresh_from_db();self.assertEqual(self.course.validation_status,'pending');self.assertEqual(self.course.validation_notes,'')
 def test_legacy_pending_label_is_also_available(self):
  self.course.validation_status='Por confirmar';self.course.save();self.assertEqual(self.group().number,1)
 def test_pending_course_enrolment_keeps_zero_history(self):
  self.assertEqual(self.enrol(self.group(),self.person),1);self.assertEqual(Enrollment.objects.count(),1);self.assertFalse(Training.objects.exists());self.assertTrue(AuditEvent.objects.exists())
 def test_duplicate_enrolment_rejected(self):
  g=self.group();self.enrol(g,self.person)
  with self.assertRaises(ValidationError):self.enrol(g,self.person)
  self.assertEqual(Enrollment.objects.count(),1)
 def test_archived_and_review_courses_stay_protected(self):
  for active,status in [(False,'pending'),(True,'blocked'),(True,'Rever')]:
   self.course.active=active;self.course.validation_status=status;self.course.save()
   with self.assertRaises(ValidationError):self.group()
  self.assertEqual(TrainingGroup.objects.count(),0)
 def test_group_numbering_and_duplicate_calendar(self):
  self.assertEqual(self.group().number,1)
  with self.assertRaises(ValidationError):self.group()
  self.assertEqual(self.group(21).number,2)
 def test_catalogue_is_compact_and_keeps_actions(self):
  r=self.client.get(reverse('planning'));self.assertEqual(r.status_code,200);self.assertContains(r,'Criar G1');self.assertContains(r,'Ver grupos (0)');self.assertNotContains(r,'Ver detalhes');self.assertNotContains(r,'Código encontrado na origem');self.assertNotContains(r,'Validação do catálogo')
 def test_reader_cannot_create_groups(self):
  reader=get_user_model().objects.create_user('reader',password='test-only');self.client.force_login(reader);self.assertEqual(self.client.get(reverse('group_create',args=[self.course.pk])).status_code,403)
 def test_archive_filter_and_history_delete_protection(self):
  self.group();self.assertFalse(course_deletion_allowed(self.course));self.course.active=False;self.course.save();r=self.client.get(reverse('planning'),{'availability':'archived'});self.assertContains(r,self.course.title);r=self.client.get(reverse('planning'),{'availability':'active'});self.assertNotContains(r,self.course.title)
 def test_group_delete_requires_confirmation_and_preserves_course_and_person(self):
  g=self.group();self.enrol(g,self.person);self.assertContains(self.client.get(reverse('course_groups',args=[self.course.pk])),'Eliminar grupo');url=reverse('group_delete',args=[g.pk]);self.assertEqual(self.client.get(url).status_code,200);self.assertTrue(TrainingGroup.objects.filter(pk=g.pk).exists())
  self.client.post(url,{'revision':g.revision,'reason':'Criado por engano'});self.assertTrue(TrainingGroup.objects.filter(pk=g.pk).exists());g.refresh_from_db()
  response=self.client.post(url,{'revision':g.revision,'reason':'Criado por engano','confirm':'ELIMINAR'});self.assertEqual(response.status_code,302);self.assertFalse(TrainingGroup.objects.filter(pk=g.pk).exists());self.assertTrue(Course.objects.filter(pk=self.course.pk).exists());self.assertTrue(Employee.objects.filter(pk=self.person.pk).exists());self.assertTrue(AuditEvent.objects.filter(action='PLANNING_DELETE').exists())
 def test_empty_course_delete_and_imported_course_protection(self):
  url=reverse('course_delete',args=[self.course.pk]);self.client.post(url,{'revision':self.course.revision,'reason':'Criada por engano','confirm':'ELIMINAR'});self.assertFalse(Course.objects.filter(pk=self.course.pk).exists())
  protected=Course.objects.create(title='Origem protegida',source_system='ensaio',source_action_id='1');self.client.post(reverse('course_delete',args=[protected.pk]),{'revision':protected.revision,'reason':'Teste','confirm':'ELIMINAR'});self.assertTrue(Course.objects.filter(pk=protected.pk).exists())
 def test_reader_cannot_delete_group_or_course(self):
  g=self.group();reader=get_user_model().objects.create_user('delete-reader',password='test-only');self.client.force_login(reader)
  for url in [reverse('group_delete',args=[g.pk]),reverse('course_delete',args=[self.course.pk])]:self.assertEqual(self.client.post(url,{'confirm':'ELIMINAR','reason':'Teste','revision':1}).status_code,403)

from datetime import date,time
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from .models import Course,ConsolidatedEdition,TrainingGroup,AuditEvent
from .group_numbers import next_number,number_from_label
from .scheduling import save_group
from .planning import course_deletion_allowed

class CatalogueButtonTests(TestCase):
    def setUp(self):
        self.staff=get_user_model().objects.create_user('synthetic-manager',is_staff=True)
        self.reader=get_user_model().objects.create_user('synthetic-reader')
        self.course=Course.objects.create(title='Synthetic compact course',validation_status='validated',validation_notes='Synthetic approval')
        self.client.force_login(self.staff)
    def edition(self,label,active=True):
        return ConsolidatedEdition.objects.create(source_key='synthetic-'+str(ConsolidatedEdition.objects.count()),course=self.course,course_reference='SYN',title=self.course.title,group_label=label,active=active)
    def values(self):
        return dict(start=date(2027,1,1),end=date(2027,1,1),dates=[date(2027,1,1)],trainer='Synthetic trainer',location='Synthetic location',capacity=20,hours_per_day=7,break_hours=1,start_time=time(8),end_time=time(16),planning_confirmation='Provisorio')
    def test_consecutive_codes(self):
        for n in range(1,6):self.edition(f'G{n}')
        self.assertEqual(next_number(self.course),6)
    def test_gaps_not_reused(self):
        for n in [1,2,4,5,6]:self.edition(str(n))
        self.assertEqual(next_number(self.course),7)
    def test_inactive_historical_code_reserved(self):
        self.edition('G12',False);self.assertEqual(next_number(self.course),13)
    def test_explicit_group_prefixes_and_whitespace(self):
        for label in [' G 004 ','Grupo 4','grupo4','004','g4']:self.assertEqual(number_from_label(label),4)
    def test_dates_arbitrary_names_and_empty_not_guessed(self):
        for label in ['',None,'edição 2026','G2 / G4','G0','-3']:self.assertIsNone(number_from_label(label))
    def test_observed_five_editions_are_not_five_distinct_codes(self):
        for label in ['1','1','1','2','']:self.edition(label)
        response=self.client.get(reverse('planning'))
        self.assertContains(response,'Ver grupos (5)');self.assertContains(response,'Criar G3')
        self.assertContains(response,'sem código numérico identificável')
    def test_cancelled_native_code_reserved(self):
        g=save_group(self.course.pk,self.values())
        from .scheduling_guard import reservation_transaction
        with reservation_transaction():g.state='Cancelada';g.save()
        self.assertEqual(next_number(self.course),2)
    def test_deleted_native_code_not_reused(self):
        g=save_group(self.course.pk,self.values())
        from .scheduling_guard import reservation_transaction
        with reservation_transaction(allow_session_deletion=True):g.sessions.all().delete();g.delete()
        self.course.refresh_from_db();self.assertEqual(next_number(self.course),2)
    def test_counter_reservation_wins(self):
        self.edition('G5');self.course.next_group=20;self.course.save();self.assertEqual(next_number(self.course),20)
    def test_preview_does_not_reserve_or_write(self):
        self.edition('Grupo 7');before=AuditEvent.objects.count()
        for _ in range(2):self.assertEqual(next_number(self.course),8)
        self.course.refresh_from_db();self.assertEqual(self.course.next_group,1);self.assertEqual(AuditEvent.objects.count(),before)
    def test_confirmed_save_uses_same_number_and_audit(self):
        self.edition('Grupo 7');g=save_group(self.course.pk,self.values())
        self.assertEqual(g.number,8);self.course.refresh_from_db();self.assertEqual(self.course.next_group,9)
        self.assertTrue(AuditEvent.objects.filter(action='PLANNING_RESERVATION').exists())
    def test_authorized_validated_course_can_open_create(self):
        response=self.client.get(reverse('group_create',args=[self.course.pk]));self.assertEqual(response.status_code,200)
        response=self.client.get(reverse('planning'));self.assertContains(response,reverse('group_create',args=[self.course.pk]))
    def test_pending_legacy_status_cannot_bypass_disabled_button(self):
        self.course.validation_status='Por confirmar';self.course.save()
        for method in [self.client.get,self.client.post]:self.assertEqual(method(reverse('group_create',args=[self.course.pk])).status_code,302)
        with self.assertRaises(ValidationError):save_group(self.course.pk,self.values())
        self.assertFalse(TrainingGroup.objects.exists())
    def test_all_unapproved_statuses_block_creation_service(self):
        for state in ['pending','blocked','Rever','Por confirmar','unrecognised']:
            self.course.validation_status=state;self.course.save()
            with self.assertRaises(ValidationError):save_group(self.course.pk,self.values())
    def test_inactive_reason_is_visible_and_server_blocks(self):
        self.course.active=False;self.course.save()
        response=self.client.get(reverse('planning'));self.assertContains(response,'Formação arquivada')
        with self.assertRaises(ValidationError):save_group(self.course.pk,self.values())
    def test_pending_reason_visible_and_accessibly_associated(self):
        self.course.validation_status='pending';self.course.save()
        response=self.client.get(reverse('planning'))
        self.assertContains(response,'Formação não validada no catálogo')
        self.assertContains(response,f'aria-describedby="create-reason-{self.course.pk}"')
        response=self.client.get(reverse('course_groups',args=[self.course.pk]));self.assertContains(response,'Formação não validada no catálogo')
    def test_reader_cannot_create_edit_or_delete_directly(self):
        self.client.force_login(self.reader)
        for name in ['group_create','course_edit','course_delete']:
            url=reverse(name,args=[self.course.pk])
            self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(self.client.post(url,{}).status_code,403)
        response=self.client.get(reverse('planning'));self.assertContains(response,'Sem autorização de gestão')
    def test_staff_has_existing_planning_authority_without_implicit_cadastral_authority(self):
        self.assertFalse(self.staff.has_perm('training.change_employee'))
        self.assertEqual(self.client.get(reverse('course_edit',args=[self.course.pk])).status_code,200)
    def test_imported_course_protected_in_ui_and_server(self):
        self.course.source_system='synthetic';self.course.source_action_id='SYN-1';self.course.save()
        self.assertFalse(course_deletion_allowed(self.course));response=self.client.get(reverse('planning'))
        self.assertContains(response,'Formação importada protegida')
        response=self.client.post(reverse('course_delete',args=[self.course.pk]),{'confirm':'ELIMINAR','reason':'Synthetic test','revision':self.course.revision})
        self.assertEqual(response.status_code,302);self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())
    def test_history_protects_deletion_even_without_import_identity(self):
        self.edition('G1',False);self.assertFalse(course_deletion_allowed(self.course))
        self.assertContains(self.client.get(reverse('planning')),'Existem grupos, participações ou histórico associado')
    def test_empty_manual_course_retains_delete_confirmation(self):
        self.assertTrue(course_deletion_allowed(self.course))
        response=self.client.get(reverse('course_delete',args=[self.course.pk]));self.assertEqual(response.status_code,200)
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())
    def test_failed_creation_rolls_back_counter_and_audit(self):
        values=self.values();values['hours_per_day']=25
        with self.assertRaises(ValidationError):save_group(self.course.pk,values)
        self.course.refresh_from_db();self.assertEqual(self.course.next_group,1)
        self.assertFalse(TrainingGroup.objects.exists());self.assertFalse(AuditEvent.objects.exists())
    def test_legacy_validated_label_keeps_authorized_creation(self):
        self.course.validation_status='Validado';self.course.save()
        self.assertEqual(self.client.get(reverse('group_create',args=[self.course.pk])).status_code,200)
        self.assertEqual(save_group(self.course.pk,self.values()).number,1)
    def test_csrf_blocks_direct_mutations_without_token(self):
        from django.test import Client
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.staff)
        for name in ['group_create','course_edit','course_delete']:
            self.assertEqual(strict.post(reverse(name,args=[self.course.pk]),{}).status_code,403)
        self.assertFalse(TrainingGroup.objects.exists())
    def test_documented_legacy_codes_are_reserved_by_exact_source_identity(self):
        self.course.source_system='epal-proagua-catalogo';self.course.source_action_id='SYN-LEGACY';self.course.save()
        index={'editions':{'one':{'action_id':'SYN-LEGACY','documented_group':'G15'},'two':{'action_id':'OTHER','documented_group':'G99'},'three':{'action_id':'SYN-LEGACY','documented_group':''}}}
        with patch('training.historical.manifest',return_value=index):self.assertEqual(next_number(self.course),16)
    def test_legacy_display_ordinals_never_become_reserved_codes(self):
        self.course.source_system='epal-proagua-catalogo';self.course.source_action_id='SYN-LEGACY';self.course.save()
        index={'editions':{'one':{'action_id':'SYN-LEGACY','documented_group':''}}}
        with patch('training.historical.manifest',return_value=index):self.assertEqual(next_number(self.course),1)
    def test_stale_course_object_cannot_reuse_deleted_or_reserved_code(self):
        Course.objects.filter(pk=self.course.pk).update(next_group=22)
        self.assertEqual(self.course.next_group,1);self.assertEqual(next_number(self.course),22)
    def test_documented_legacy_g1_with_blank_consolidated_label_proposes_g2(self):
        self.edition('');self.course.source_system='epal-proagua-catalogo';self.course.source_action_id='SYN-WORKSHOP';self.course.save()
        index={'editions':{'one':{'action_id':'SYN-WORKSHOP','documented_group':'G1'}}}
        with patch('training.historical.manifest',return_value=index):
            response=self.client.get(reverse('planning'));self.assertContains(response,'Ver grupos (1)');self.assertContains(response,'Criar G2')

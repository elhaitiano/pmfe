from datetime import date,time
from django.test import TestCase
from django.core.exceptions import ValidationError,PermissionDenied
from django.urls import reverse
from unittest.mock import patch
import copy
from . import group_validation as service
from .models import Course,ConsolidatedEdition,TrainingGroup,HistoricalInterfaceLink,AuditEvent,Training
from .test_group_validation import GroupValidationTests as Fixtures
from .group_numbers import next_number

class CourseValidationTests(TestCase):
    setUp=Fixtures.setUp
    member=Fixtures.member

    def extra_edition(self,label):
        return ConsolidatedEdition.objects.create(source_key='synthetic-'+str(label),course=self.course,course_reference=self.course.code,title=self.course.title,group_label=str(label),start=date(2026,1,1),end=date(2026,1,2))

    def test_course_preview_uses_existing_flow_without_writes(self):
        t,p=self.member();before=service.values(t);events=AuditEvent.objects.count()
        response=self.client.get(self.url,{'course':self.course.pk})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Confirmar validação colectiva')
        self.assertEqual([g['key'] for g in response.context['groups']],[self.key])
        t.refresh_from_db();self.assertEqual(service.values(t),before);self.assertEqual(AuditEvent.objects.count(),events)

    def test_signature_projection_is_equivalent_and_does_not_mutate_nested_sources(self):
        t,p=self.member()
        t.source_data={'nested':{'items':[1,{'kept':True}]},'_EPAL_CONSOLIDATED_FACTS':{'sync_version':7,'nested':{'kept':True}},service.MARKER:{'kept':True}}
        before=copy.deepcopy(t.source_data)
        optimized=service.dependency_fingerprint(t,p,None,self.edition,self.course)
        with patch.object(service,'fingerprint_values',side_effect=service.values):
            original=service.dependency_fingerprint(t,p,None,self.edition,self.course)
        self.assertEqual(optimized,original);self.assertEqual(t.source_data,before)

    def test_all_course_groups_beyond_page_and_manual_limit(self):
        for n in range(2,13):self.extra_edition(n)
        response=self.client.get(self.url,{'course':self.course.pk})
        self.assertEqual(response.status_code,200);self.assertEqual(len(response.context['groups']),12)
        with self.assertRaises(ValidationError):service.preview(self.user,service.course_keys(self.course.pk))

    def test_course_confirmation_preserves_certificates_and_original_values(self):
        t,p=self.member();before=service.values(t);review=service.preview(self.user,course_id=self.course.pk)
        self.assertEqual(len(service.apply(self.user,review['token'],True)),1)
        t.refresh_from_db();after=service.values(t)
        for k in before:
            if k not in ('source_data','version'):self.assertEqual(before[k],after[k],k)
        self.assertEqual(AuditEvent.objects.filter(action=service.ACTION).count(),1)
        self.assertEqual(t.source_data[service.MARKER]['decisions']['academic'],'Por validar')

    def test_course_membership_change_invalidates_signed_preview(self):
        t,p=self.member();token=service.preview(self.user,course_id=self.course.pk)['token'];self.extra_edition(2)
        with self.assertRaises(ValidationError):service.apply(self.user,token,True)
        self.assertFalse(AuditEvent.objects.filter(action=service.ACTION).exists())

    def test_course_scope_cannot_be_mixed_or_used_without_permission(self):
        self.assertEqual(self.client.get(self.url,{'course':self.course.pk,'group':self.key}).status_code,400)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(self.url,{'course':self.course.pk}).status_code,403)
        with self.assertRaises(PermissionDenied):service.preview(self.reader,course_id=self.course.pk)

    def test_original_cards_table_selection_and_permission_visibility(self):
        self.member();response=self.client.get(reverse('planning'))
        for text in ['Ver grupos (1)','Criar G2','Editar acção','Eliminar formação']:
            self.assertContains(response,text)
        self.assertNotContains(response,'id="grupos"')
        response=self.client.get(reverse('course_groups',args=[self.course.pk]))
        self.assertContains(response,'Validar seleccionados');self.assertContains(response,'data-select-group')
        self.client.force_login(self.reader);response=self.client.get(reverse('planning'))
        self.assertNotContains(response,'Validar seleccionados');self.assertNotContains(response,'data-select-group')

    def test_course_group_limit_is_safe_and_explicit(self):
        for n in range(2,102):self.extra_edition(n)
        with self.assertRaises(ValidationError):service.preview(self.user,course_id=self.course.pk)

    def test_large_course_uses_one_existing_atomic_confirmation(self):
        first=self.edition;second=self.extra_edition(2)
        for n in range(1,502):
            self.edition=first if n<=250 else second
            self.member(n)
        with self.assertRaises(ValidationError):service.preview(self.user,service.course_keys(self.course.pk))
        preview=service.preview(self.user,course_id=self.course.pk)
        self.assertEqual(preview['eligible'],501)
        self.assertEqual(len(service.apply(self.user,preview['token'],True)),501)
        self.assertEqual(AuditEvent.objects.filter(action=service.ACTION).count(),501)
        self.assertFalse(Training.objects.exclude(result='Pendente').exists())

    def test_course_row_limit_cannot_be_bypassed(self):
        rows=[{'training_id':n} for n in range(service.MAX_COURSE_ROWS+1)]
        with patch.object(service,'collect',return_value={'rows':rows,'pending':[]}):
            with self.assertRaises(ValidationError):service.preview(self.user,course_id=self.course.pk)

    def test_catalogue_has_no_calendar_or_replacement_group_panel(self):
        response=self.client.get(reverse('planning'))
        self.assertContains(response,'plan-course-original')
        for text in ['id="grupos"','group-selection','data-select-group','Indicadores de planeamento','attendance-lists']:
            self.assertNotContains(response,text)
        self.assertNotIn('page',response.context)
        self.assertContains(response,reverse('course_groups',args=[self.course.pk]))

    def test_course_page_only_contains_selected_course_and_ignores_other_course_parameter(self):
        other=Course.objects.create(code='OTHER-SCOPE',title='Other course only')
        hidden=ConsolidatedEdition.objects.create(source_key='other-scope',course=other,course_reference=other.code,title=other.title,group_label='2',start=date(2026,1,1),end=date(2026,1,2))
        url=reverse('course_groups',args=[self.course.pk])
        response=self.client.get(url,{'course':other.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['selected_course'].pk,self.course.pk)
        self.assertEqual([g.course_id for g in response.context['page']],[self.course.pk])
        self.assertNotContains(response,other.title)
        self.assertContains(response,reverse('group_validation')+'?course='+str(self.course.pk))
        self.assertContains(response,'Criar novo grupo')
        self.assertNotContains(response,'plan-course-original')

    def test_group_page_requires_authentication_and_get(self):
        url=reverse('course_groups',args=[self.course.pk])
        self.assertEqual(self.client.post(url,{}).status_code,405)
        self.client.logout();self.assertEqual(self.client.get(url).status_code,302)

    def test_group_page_search_and_pagination_keep_scope(self):
        for n in range(2,24):self.extra_edition(n)
        response=self.client.get(reverse('course_groups',args=[self.course.pk]),{'page':2})
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['page'].has_previous())
        self.assertTrue(all(g.course_id==self.course.pk for g in response.context['page']))
        response=self.client.get(reverse('course_groups',args=[self.course.pk]),{'q':'not a matching title'})
        self.assertEqual(len(response.context['page']),0)
        self.assertContains(response,reverse('group_validation')+'?course='+str(self.course.pk))

    def test_legacy_course_link_redirects_to_dedicated_page(self):
        response=self.client.get(reverse('planning'),{'course':self.course.pk,'q':'test'})
        self.assertRedirects(response,reverse('course_groups',args=[self.course.pk])+'?q=test',fetch_redirect_response=False)

class GroupNumberTests(TestCase):
    def setUp(self):self.course=Course.objects.create(code='NUMBER',title='Synthetic numbering',validation_status='validated')
    def historical(self,label,active=True):
        return ConsolidatedEdition.objects.create(source_key='number-'+str(label),course=self.course,course_reference='NUMBER',title=self.course.title,group_label=label,start=date(2026,1,1),end=date(2026,1,2),active=active)
    def test_next_number_considers_all_historical_codes_without_writing(self):
        self.historical('G1');self.historical('2');self.historical('G3',False)
        self.assertEqual(next_number(self.course),4);self.course.refresh_from_db();self.assertEqual(self.course.next_group,1)
    def test_counter_preserves_deleted_or_reserved_codes(self):
        self.historical('G3');self.course.next_group=12;self.course.save()
        self.assertEqual(next_number(self.course),12)
    def test_non_numeric_labels_are_not_guessed(self):
        self.historical('Grupo especial');self.assertEqual(next_number(self.course),1)
    def test_creation_uses_same_forecast_and_reserves_only_on_confirmation(self):
        from .scheduling import save_group
        self.historical('G3')
        before=Course.objects.get(pk=self.course.pk).next_group
        self.assertEqual(next_number(self.course),4);self.assertEqual(Course.objects.get(pk=self.course.pk).next_group,before)
        values={'start':date(2026,1,1),'end':date(2026,1,1),'dates':[date(2026,1,1)],'trainer':'Synthetic trainer','provider':'Synthetic','location':'Synthetic location','capacity':20,'hours_per_day':7,'break_hours':1,'start_time':time(8),'end_time':time(16),'planning_confirmation':'Provisorio'}
        group=save_group(self.course.pk,values);self.assertEqual(group.number,4)
        self.course.refresh_from_db();self.assertEqual(self.course.next_group,5)

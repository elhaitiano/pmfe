from datetime import date
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission, Group
from django.urls import reverse
from .models import Employee, Training, AuditEvent
from .forms import EmployeeForm
from .documents import delivery_summary

class MissingAttendanceTests(TestCase):
    def setUp(self):
        self.employee=Employee.objects.create(number='POST-AUDIT',name='Pessoa fictícia')
        self.training=Training.objects.create(employee=self.employee,title='Curso fictício',area='Técnica',start=date(2026,1,1),end=date(2026,1,2),days=2,hours_per_day=7,attendance_confirmed=True,attendance=None,grade=None)
        self.user=get_user_model().objects.create_superuser('post-audit-admin',password=None)

    def test_missing_confirmed_attendance_remains_unknown(self):
        summary=delivery_summary(self.training)
        self.assertEqual(summary['participation'],'Participação sem prova suficiente')
        self.assertIsNone(summary['verified_absences'])
        self.assertIsNone(summary['grade'])
        self.training.refresh_from_db();self.assertIsNone(self.training.attendance)

    def test_profile_history_and_print_render_without_data_changes(self):
        self.client.force_login(self.user)
        for name in ('employee_data','employee_detail','employee_print'):
            self.assertEqual(self.client.get(reverse(name,args=[self.employee.pk])).status_code,200)
        self.training.refresh_from_db();self.assertIsNone(self.training.grade);self.assertIsNone(self.training.attendance)

    def test_zero_and_positive_attendance_have_distinct_meanings(self):
        for value,label in [(0,'Ausência confirmada'),(1,'Comprovada')]:
            self.training.attendance=value
            self.assertEqual(delivery_summary(self.training)['participation'],label)

    def test_legacy_history_excludes_missing_attendance_from_denominator(self):
        from .historical import metrics
        row={'training':self.training,'level':'unmatched','binding':None,'state':None,'learner_delivery':None,'age_band':'unknown'}
        result=metrics([row])
        self.assertEqual(result['attendance_coverage'],0)
        self.assertIsNone(result['attendance'])

class LegacyEmployeeAuthorizationTests(TestCase):
    def setUp(self):
        self.employee=Employee.objects.create(number='AUTH-SYN',name='Cadastro fictício')
        self.staff=get_user_model().objects.create_user('staff-no-model-perms',is_staff=True)
        self.edit=reverse('employee_edit',args=[self.employee.pk]);self.create=reverse('employee_create')
        self.payload={field.name:field.value() if field.value() is not None else '' for field in EmployeeForm(instance=self.employee)}
        self.payload['company']='Empresa fictícia autorizada'

    def grant(self,user,*names,group=False):
        permissions=Permission.objects.filter(content_type__app_label='training',codename__in=names)
        if group:
            team=Group.objects.create(name='Cadastro autorizado');team.permissions.set(permissions);user.groups.add(team)
        else:user.user_permissions.set(permissions)

    def test_staff_without_permissions_cannot_access_or_mutate_by_any_method(self):
        self.client.force_login(self.staff)
        for url in (self.edit,self.create):
            for method in ('get','post','put','patch','delete'):
                response=getattr(self.client,method)(url,self.payload if method=='post' else {})
                self.assertEqual(response.status_code,403,(url,method))
        self.employee.refresh_from_db();self.assertEqual(self.employee.company,'')
        self.assertEqual(Employee.objects.count(),1);self.assertFalse(AuditEvent.objects.exists())

    def test_reader_and_anonymous_are_not_admitted(self):
        self.assertEqual(self.client.get(self.edit).status_code,302)
        reader=get_user_model().objects.create_user('read-only')
        self.client.force_login(reader)
        self.assertEqual(self.client.post(self.edit,self.payload).status_code,403)

    def test_view_permission_alone_is_not_write_permission(self):
        self.grant(self.staff,'view_employee');self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.edit,self.payload).status_code,403)

    def test_change_without_view_is_denied(self):
        self.grant(self.staff,'change_employee');self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.edit,self.payload).status_code,403)

    def test_group_authorized_editor_can_save_but_not_create(self):
        self.grant(self.staff,'view_employee','change_employee',group=True);self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.edit,self.payload).status_code,302)
        self.employee.refresh_from_db();self.assertEqual(self.employee.company,self.payload['company'])
        self.assertEqual(self.client.get(self.create).status_code,403)
        self.assertEqual(self.client.put(self.edit,{}).status_code,405)

    def test_add_permission_does_not_grant_change(self):
        self.grant(self.staff,'view_employee','add_employee');self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.create).status_code,200)
        self.assertEqual(self.client.get(self.edit).status_code,403)

    def test_authorized_creator_can_submit_new_employee(self):
        self.grant(self.staff,'view_employee','add_employee');self.client.force_login(self.staff)
        payload={**self.payload,'number':'AUTH-NEW','name':'Nova pessoa fictícia'}
        self.assertEqual(self.client.post(self.create,payload).status_code,302)
        self.assertEqual(Employee.objects.count(),2)
        self.assertTrue(AuditEvent.objects.filter(action='ADD_EMPLOYEE').exists())

    def test_nonstaff_and_inactive_users_are_not_authorized(self):
        from .views import employee_permission_required
        from django.test import RequestFactory
        from django.http import HttpResponse
        from django.core.exceptions import PermissionDenied
        view=employee_permission_required('change_employee')(lambda request:HttpResponse())
        for staff,active in [(False,True),(True,False)]:
            user=get_user_model().objects.create_user('blocked-'+str(staff),is_staff=staff,is_active=active)
            self.grant(user,'view_employee','change_employee')
            request=RequestFactory().post(self.edit);request.user=user
            with self.assertRaises(PermissionDenied):view(request)

class PresentationRegressionTests(TestCase):
    setUp=MissingAttendanceTests.setUp
    def test_single_primary_heading_and_named_certificate_filters(self):
        self.client.force_login(self.user)
        for name in ('planning','historical_index','certificate_archive','planning_resources'):
            response=self.client.get(reverse(name));self.assertEqual(response.status_code,200)
            self.assertEqual(response.content.decode().count('<h1'),1,name)
        content=self.client.get(reverse('certificate_archive')).content.decode()
        self.assertIn('for="certificate-q"',content);self.assertIn('id="certificate-q"',content)
        self.assertIn('for="certificate-state"',content)

    def test_unclassified_hours_are_not_reported_as_missing_hours(self):
        self.training.area='';self.training.attendance=1;self.training.save()
        self.client.force_login(self.user)
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'Existem horas frequentadas sem área de formação atribuída.')
        self.assertNotContains(response,'Sem carga horária registada.')

    def test_document_styles_are_loaded_and_null_presence_is_not_displayed_confirmed(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('employee_data',args=[self.employee.pk]))
        self.assertContains(response,'pmf/post-audit.css')
        self.assertContains(response,'Participação sem prova suficiente')
        self.assertNotContains(response,'Presenças confirmadas: None')

    def test_admin_accessibility_styles_are_loaded(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('admin:index')),'pmf/admin-accessibility.css')

class ConsolidatedPerformanceRegressionTests(TestCase):
    def test_light_summaries_preserve_values_and_full_evidence_remains_accessible(self):
        from .models import OfficialRosterEntry,ConsolidatedEdition,ConsolidatedParticipation
        from .consolidation import participations,summary_participations,stats
        person=Employee.objects.create(number='PERF-SYN',name='Pessoa fictícia',source_data={'private':'preserved'})
        OfficialRosterEntry.objects.create(employee=person,current=True,source_sha256='a'*64,source_row=1)
        edition=ConsolidatedEdition.objects.create(source_key='perf-ed',course_reference='PERF',title='Curso fictício',evidence={'original':'preserved'})
        ConsolidatedParticipation.objects.create(source_key='perf-part',employee=person,edition=edition,grades=[{'stage':'final','value':'0','scale':'20'}],evidence={'validated_certificates':[]})
        with self.assertNumQueries(1):rows=list(summary_participations())
        self.assertIn('evidence',rows[0].get_deferred_fields())
        self.assertIn('source_data',rows[0].employee.get_deferred_fields())
        self.assertEqual(stats(rows),stats(participations()))
        self.assertEqual(rows[0].evidence,{'validated_certificates':[]})
        self.assertEqual(rows[0].employee.source_data,{'private':'preserved'})

    def test_large_pdf_table_uses_streamlined_flowable_without_losing_rows(self):
        from .executive_pdf import table
        from reportlab.platypus import LongTable
        result=table(['Coluna'],[['zero'],['desconhecido']],long=True)
        self.assertIsInstance(result,LongTable)
        self.assertEqual(len(result._cellvalues),3)

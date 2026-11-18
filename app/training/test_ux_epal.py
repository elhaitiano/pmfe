from datetime import date
from decimal import Decimal
from django.test import TestCase,RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse,resolve
from .models import Employee,Training,Course,ConsolidatedEdition,ConsolidatedParticipation,HistoricalInterfaceLink
from .ux_presentation import display_rows,grade,competencies,navigation

class UXPresentationTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(number='UX-SYN',name='Formando sintético')
        self.reader=get_user_model().objects.create_user('ux-reader')
        self.admin=get_user_model().objects.create_superuser('ux-admin',password='synthetic')
        self.course=Course.objects.create(title='Excel intermédio',area='Tecnologias de Informação')
    def training(self,**kwargs):
        return Training.objects.create(employee=self.person,course=self.course,title='EXCEL INTERMÉDIO',start=date(2025,1,1),end=date(2025,1,2),days=2,hours_per_day=7,attendance_confirmed=True,attendance=2,**kwargs)
    def test_missing_grade_is_absent_from_competencies_and_zero_is_real(self):
        t=self.training();self.assertEqual(competencies([t]),[])
        t.grade=0;self.assertEqual(competencies([t])[0]['percent'],0);self.assertEqual(t.ux_grade_label,'0/20')
    def test_documented_percent_and_twenty_preserve_source_scales(self):
        t=self.training(grade=Decimal(17));self.assertEqual(display_rows([t])[0].ux_grade_percent,85)
        t.source_data={'_EPAL_CONSOLIDATED_FACTS':{'grades':[{'stage':'final','value':'85','scale':'100'}]}}
        self.assertEqual(display_rows([t])[0].ux_grade_label,'85/100')
    def test_unknown_or_ambiguous_scale_never_creates_grade(self):
        t=self.training();t.source_data={'_EPAL_CONSOLIDATED_FACTS':{'grades':[{'stage':'final','value':'17','scale':'0'}]}};self.assertIsNone(grade(t))
        t.source_data={'_EPAL_CONSOLIDATED_FACTS':{'grades':[{'stage':'final','value':'17','scale':'20'},{'stage':'final','value':'18','scale':'20'}]}};self.assertIsNone(grade(t))
    def test_distinct_editions_are_retained_and_same_canonical_row_not_repeated(self):
        a=self.training();b=self.training();b.start=date(2025,2,1);b.end=date(2025,2,2);b.save()
        for n,t in enumerate([a,b]):
            e=ConsolidatedEdition.objects.create(source_key='ux-e'+str(n),course=self.course,title=self.course.title,start=t.start,end=t.end)
            p=ConsolidatedParticipation.objects.create(source_key='ux-p'+str(n),employee=self.person,edition=e)
            HistoricalInterfaceLink.objects.create(training=t,participation=p)
        self.assertEqual(len(display_rows([a,b])),2);self.assertEqual(len(display_rows([a,a,b])),2)
        self.assertEqual(a.ux_title,'Excel intermédio');a.refresh_from_db();self.assertEqual(a.title,'EXCEL INTERMÉDIO')
    def test_unknown_attendance_hours_do_not_use_certificate_hours(self):
        t=self.training();t.attendance_confirmed=False;t.attendance=None;t.source_data={'_validated_certificates':[{'hours':'30'}]}
        self.assertIsNone(display_rows([t])[0].ux_hours)
    def test_reader_has_clean_eight_column_history_and_no_admin_tools(self):
        self.training(grade=17);self.client.force_login(self.reader);r=self.client.get(reverse('employee_data',args=[self.person.pk]))
        self.assertEqual(r.status_code,200);self.assertContains(r,'17/20');self.assertContains(r,'Consultar o histórico completo');self.assertNotContains(r,'Gerir documentos e estados')
        self.assertEqual(self.client.get(reverse('ux_administration')).status_code,403)
    def test_authorized_document_manager_keeps_existing_action(self):
        staff=get_user_model().objects.create_user('ux-doc',is_staff=True);staff.user_permissions.add(Permission.objects.get(codename='change_trainingdocumentstate'))
        t=self.training();self.client.force_login(staff);r=self.client.get(reverse('employee_data',args=[self.person.pk]));self.assertContains(r,reverse('document_delivery',args=[t.pk]));response=self.client.get(reverse('ux_administration'));self.assertEqual(response.status_code,200);self.assertContains(response,'Documentação dos formandos')
    def test_back_destination_rejects_external_and_preserves_internal_filters(self):
        factory=RequestFactory();request=factory.get('/planeamento/',{'return_to':'https://example.invalid/'},HTTP_HOST='testserver');request.user=self.reader;request.resolver_match=resolve('/planeamento/')
        self.assertEqual(navigation(request)['ux_back_url'],reverse('home'))
        request=factory.get('/planeamento/',HTTP_HOST='testserver',HTTP_REFERER='http://testserver/?q=teste&page=2');request.user=self.reader;request.resolver_match=resolve('/planeamento/')
        self.assertEqual(navigation(request)['ux_back_url'],'/?q=teste&page=2')
    def test_administration_is_read_only_get(self):
        self.client.force_login(self.admin);self.assertEqual(self.client.post(reverse('ux_administration')).status_code,405)

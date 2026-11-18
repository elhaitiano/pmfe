"""Synthetic historical navigation; no production DB or Excel reads."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import hashlib
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import TextField
from django.db.models.functions import Cast
from django.test import TestCase,override_settings
from django.urls import reverse
from .models import Employee,Training,Course,TrainingGroup,Enrollment,GroupSession,TrainingDocumentState
from . import historical as h

@override_settings(PMF_HISTORICAL_ENABLED=True)
class HistoricalTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('history-reader',password='synthetic')
        self.admin=get_user_model().objects.create_superuser('history-admin',password='synthetic')
        self.employee=Employee.objects.create(number='SYN-1',name='Synthetic person',direction='Synthetic direction',birth_date=date(1990,6,15),source_data={'Validação Cadastro EPAL':'Definitivo — synthetic','Data de Nascimeto':'1990-06-15'})
        self.course=Course.objects.create(title='Synthetic action',source_system='epal-proagua-catalogo',source_action_id='SYN-A',validation_status='pending')
        self.training=Training.objects.create(employee=self.employee,title='Synthetic action',start=date(2026,6,15),end=date(2026,6,16),days=2,hours_per_day=Decimal('6.5'),attendance=1,source_key='pilar3b:SYN-1',source_data={'preserved':'source'},status='Concluído')
        t=Training.objects.annotate(source_json_text=Cast('source_data',TextField())).get(pk=self.training.pk)
        self.manifest={'excluded_keys':['pilar3b:PF-00285','pilar3b:PF-00434','pilar3b:PF-00459','pilar3b:PF-01388','pilar3b:PF-01854'],'bindings':{t.source_key:{'edition':'SYN-E','phase':'SYN-F','action_id':'SYN-A','level':'confirmed','method':'Explicit source group','signature':h.signature(t)}},'editions':{'SYN-E':{'id':'SYN-E','action_id':'SYN-A','display_id':'HR-SYNTHETIC','title':'Synthetic action','documented_group':'G7','start':'2026-06-15','end':'2026-06-16','location':'Synthetic local','trainer':'Synthetic trainer'}}}
        self.mock=patch('training.historical.manifest',return_value=self.manifest);self.mock.start();self.addCleanup(self.mock.stop)
        self.manifest['bindings'][t.source_key]['employee_number_sha256']=hashlib.sha256(self.employee.number.encode()).hexdigest()

    def test_records_are_three_queries_without_n_plus_one(self):
        with self.assertNumQueries(3):rows=h.records()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['level'],'confirmed')

    def test_age_is_historical_not_current(self):
        self.assertEqual(h.age_band(self.employee,date(2010,6,14)),'u25')
        self.assertEqual(h.age_band(self.employee,date(2026,6,15)),'35')

    def test_age_boundaries(self):
        for age,band in [(24,'u25'),(25,'25'),(34,'25'),(35,'35'),(44,'35'),(45,'45'),(54,'45'),(55,'55'),(64,'55'),(65,'65')]:
            with self.subTest(age=age):self.assertEqual(h.age_band(self.employee,date(1990+age,6,15)),band)

    def test_unvalidated_missing_or_future_birth_is_unknown(self):
        self.employee.source_data={};self.assertEqual(h.age_band(self.employee,date(2026,1,1)),'unknown')
        self.employee.birth_date=None;self.assertEqual(h.age_band(self.employee,date(2026,1,1)),'unknown')

    def test_filters_compose(self):
        rows=h.records();self.assertEqual(len(h.filtered(rows,{'year':'2026','action':'SYN-A','edition':'SYN-E','direction':'Synthetic direction','age_band':'35'})),1)
        self.assertEqual(h.filtered(rows,{'age_band':'65'}),[])

    def test_same_dates_never_create_membership(self):
        self.manifest['bindings']={};self.assertEqual(h.records()[0]['level'],'unmatched')
        self.assertEqual(h.metrics(h.records())['groups'],0)

    def test_changed_original_fields_block_binding(self):
        Training.objects.filter(pk=self.training.pk).update(title='Changed original')
        self.assertEqual(h.records()[0]['level'],'unmatched')

    def test_revision_and_confirmation_do_not_destroy_binding(self):
        Training.objects.filter(pk=self.training.pk).update(version=2,attendance_confirmed=True)
        self.assertEqual(h.records()[0]['level'],'confirmed')

    def test_duplicate_key_excluded_without_deletion(self):
        self.training.pk=None;self.training.source_key='pilar3b:PF-00285';self.training.save()
        self.assertEqual(len(h.records()),1);self.assertEqual(Training.objects.count(),2)

    def test_probable_group_not_counted_confirmed(self):
        self.manifest['bindings']['pilar3b:SYN-1']['level']='probable'
        stats=h.metrics(h.records());self.assertEqual(stats['groups'],0);self.assertEqual(stats['probable'],1)

    def test_consultation_groups_count_probable_without_approving_membership(self):
        self.manifest['bindings']['pilar3b:SYN-1']['level']='probable'
        rows=h.records();stats=h.metrics(rows)
        self.assertEqual(stats['consultation_groups'],1)
        self.assertEqual(stats['groups'],0)
        h.attach_counts([self.course]);self.assertEqual(self.course.historical_count,1)
        self.assertEqual(TrainingGroup.objects.count(),0)
        self.assertEqual(Enrollment.objects.count(),0)

    def test_generated_group_label_is_stable_and_preserves_documented_code(self):
        rows=h.records()
        self.assertEqual(h.editions(rows)[0]['edition']['presentation_label'],'G7')
        self.manifest['editions']['SYN-E']['documented_group']=''
        self.assertEqual(h.editions(rows)[0]['edition']['presentation_label'],'Grupo 1')
        self.assertEqual(h.editions(h.filtered(rows,{'action':'SYN-A'}))[0]['edition']['presentation_label'],'Grupo 1')

    def test_unknown_attendance_and_grades_not_zero(self):
        stats=h.metrics(h.records());self.assertIsNone(stats['attendance']);self.assertEqual(stats['attendance_coverage'],0);self.assertIsNone(stats['average']);self.assertIsNone(stats['actual_participant_hours'])

    def test_decimal_hours_and_confirmed_denominator(self):
        Training.objects.filter(pk=self.training.pk).update(attendance_confirmed=True)
        stats=h.metrics(h.records());self.assertEqual(stats['attendance'],50);self.assertEqual(stats['actual_participant_hours'],Decimal('6.5'));self.assertEqual(stats['planned_participant_hours'],Decimal('13'));self.assertEqual(stats['documented_segment_hours'],Decimal('13'))

    def test_issuance_and_deliveries_independent(self):
        TrainingDocumentState.objects.create(training=self.training,issued=True,reference='Synthetic source')
        stats=h.metrics(h.records());self.assertEqual(stats['issued'],1);self.assertEqual(stats['learner_delivered'],0);self.assertEqual(stats['hr_delivered'],0)

    def test_confirmed_delivery_preserved_and_explicit_no_is_not_unknown(self):
        Training.objects.filter(pk=self.training.pk).update(certified=True)
        TrainingDocumentState.objects.create(training=self.training,issued=True,learner_delivered=None)
        self.assertEqual(h.metrics(h.records())['learner_delivered'],1)
        TrainingDocumentState.objects.filter(training=self.training).update(learner_delivered=False)
        self.assertIs(h.records()[0]['learner_delivery'],False)
        self.assertEqual(h.metrics(h.records())['learner_delivered'],0)

    def test_employee_number_change_invalidates_binding(self):
        Employee.objects.filter(pk=self.employee.pk).update(number='SYN-CHANGED')
        self.assertEqual(h.records()[0]['level'],'unmatched')

    def test_authentication_and_both_permissions_required(self):
        url=reverse('historical_index');self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.user);self.assertEqual(self.client.get(url).status_code,403)
        self.user.user_permissions.add(Permission.objects.get(codename='view_training'))
        self.assertEqual(self.client.get(url).status_code,403)
        self.user.user_permissions.add(Permission.objects.get(codename='view_employee'))
        self.assertEqual(self.client.get(url).status_code,200)

    def test_get_only_and_feature_gate(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('historical_index'),{}).status_code,405)
        with override_settings(PMF_HISTORICAL_ENABLED=False):self.assertEqual(self.client.get(reverse('historical_index')).status_code,404)

    def test_nominal_detail_links_and_accessible_filters(self):
        self.client.force_login(self.admin);response=self.client.get(reverse('historical_detail',args=['SYN-E']))
        self.assertContains(response,'Synthetic person');self.assertContains(response,reverse('employee_detail',args=[self.employee.pk]));self.assertContains(response,'name="age_band"');self.assertContains(response,'identificador de apresentação');self.assertContains(response,'Por confirmar')

    def test_pdf_uses_same_filters_and_does_not_archive(self):
        self.client.force_login(self.admin);response=self.client.get(reverse('historical_pdf')+'?year=2026&age_band=35')
        self.assertEqual(response.status_code,200);self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'));response.close()

    def test_requests_do_not_create_operational_or_historical_data(self):
        self.client.force_login(self.admin);before=list(Training.objects.values());self.client.get(reverse('historical_index'));self.client.get(reverse('historical_detail',args=['SYN-E']))
        self.assertEqual(before,list(Training.objects.values()));self.assertEqual(TrainingGroup.objects.count(),0);self.assertEqual(Enrollment.objects.count(),0);self.assertEqual(GroupSession.objects.count(),0)

    def test_unknown_edition_404(self):
        self.client.force_login(self.admin);self.assertEqual(self.client.get(reverse('historical_detail',args=['invented-G1'])).status_code,404)

    def test_responsive_layout_and_keyboard_in_browser(self):
        from pathlib import Path
        from django.conf import settings
        browser_path=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
        if not browser_path.exists():self.skipTest('Edge indisponível para verificação visual isolada')
        from playwright.sync_api import sync_playwright
        self.client.force_login(self.admin)
        html=self.client.get(reverse('historical_detail',args=['SYN-E'])).content.decode()
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=str(browser_path),headless=True)
            try:
                page=browser.new_page();page.route('**/*',lambda route:route.abort())
                page.set_content(html,wait_until='domcontentloaded')
                for name in ['tailwind.css','platform-original.css','adapters.css','batch.css','planning.css','historical.css']:
                    page.add_style_tag(path=str(settings.BASE_DIR/'static/pmf'/name))
                for width in (360,768,1024,1440):
                    page.set_viewport_size({'width':width,'height':900})
                    for select in page.locator('.history-filters select').all():
                        box=select.bounding_box();self.assertGreaterEqual(box['x'],0);self.assertLessEqual(box['x']+box['width'],width+1)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                select=page.locator('select[name=age_band]');select.focus();page.keyboard.press('ArrowDown')
                self.assertEqual(page.locator(':focus').get_attribute('name'),'age_band')
            finally:browser.close()

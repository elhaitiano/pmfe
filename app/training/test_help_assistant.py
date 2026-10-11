import json
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

class HelpAssistantTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('help-admin', is_staff=True)
        self.reader = get_user_model().objects.create_user('help-reader')

    def test_admin_help_in_core_pages(self):
        self.client.force_login(self.admin)
        for name in ['home','planning','batch_index','dashboard','executive_reports','employee_data_select']:
            with self.subTest(page=name):
                response=self.client.get(reverse(name))
                self.assertEqual(response.status_code,200)
                self.assertContains(response,'id="epalHelpToggle"')
                self.assertContains(response,'data-staff="true"')
                self.assertContains(response,'data-help-route="batch_index"')

    def test_reader_has_guidance_but_no_results_shortcut(self):
        self.client.force_login(self.reader)
        response=self.client.get(reverse('planning'))
        self.assertContains(response,'data-staff="false"')
        self.assertNotContains(response,'data-help-route="batch_index"')
        self.assertEqual(self.client.get(reverse('batch_index')).status_code,403)

    def test_no_assistant_before_login(self):
        response=self.client.get(reverse('login'))
        self.assertNotContains(response,'id="epalHelpToggle"')
        self.assertEqual(self.client.get(reverse('planning')).status_code,302)

    def test_guides_routes_and_static_resources_are_local(self):
        root=Path(settings.BASE_DIR)
        guides=json.loads((root/'static/pmf/help-guides.json').read_text())
        self.assertEqual(len(guides),14)
        self.assertIn('delete_course', {guide['id'] for guide in guides})
        for guide in guides:
            self.assertTrue(reverse(guide['route']).startswith('/'))
            self.assertGreaterEqual(len(guide['steps']),2)
        js=(root/'static/pmf/help-assistant.js').read_text()
        self.assertNotIn('http://',js)
        self.assertNotIn('https://',js)
        self.assertNotIn('innerHTML',js)

class AssistantAdministrationTests(TestCase):
    def setUp(self):
        from .models import AssistantGuide
        self.Guide=AssistantGuide
        self.admin=get_user_model().objects.create_user('guide-admin',is_staff=True)
        self.reader=get_user_model().objects.create_user('guide-reader')
        self.guide=AssistantGuide.objects.first()

    def payload(self,guide=None,**extra):
        guide=guide or self.guide
        data={'slug':guide.slug,'title':'Orientação revista','keyword_text':'nova pergunta\npergunta especial',
              'route':'planning','enabled':'on','revision':guide.revision,
              'steps-TOTAL_FORMS':'1','steps-INITIAL_FORMS':'1','steps-MIN_NUM_FORMS':'0','steps-MAX_NUM_FORMS':'20',
              'steps-0-text':'Escolha a formação e confirme o grupo.','steps-0-target':'#navGestao'}
        data.update(extra);return data

    def test_reader_and_anonymous_cannot_manage_or_write(self):
        for authenticated in [False,True]:
            if authenticated:self.client.force_login(self.reader)
            for name,args in [('assistant_admin',[]),('assistant_create',[]),('assistant_edit',[self.guide.pk])]:
                url=reverse(name,args=args)
                self.assertEqual(self.client.get(url).status_code,403 if authenticated else 302)
                self.assertEqual(self.client.post(url,self.payload()).status_code,403 if authenticated else 302)
        self.guide.refresh_from_db();self.assertEqual(self.guide.revision,1)

    def test_publish_updates_authenticated_help_and_audit(self):
        from .models import AuditEvent
        self.client.force_login(self.admin)
        response=self.client.post(reverse('assistant_edit',args=[self.guide.pk]),self.payload())
        self.assertEqual(response.status_code,302)
        self.guide.refresh_from_db();self.assertEqual(self.guide.revision,2)
        self.assertTrue(AuditEvent.objects.filter(action='ASSISTENTE').exists())
        self.client.force_login(self.reader)
        response=self.client.get(reverse('assistant_guides'))
        item=next(g for g in response.json() if g['id']==self.guide.slug)
        self.assertEqual(item['title'],'Orientação revista');self.assertEqual(item['revision'],2)
        self.assertEqual(response['Cache-Control'],'no-store, private')

    def test_deactivate_and_reject_stale_editor(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('assistant_edit',args=[self.guide.pk]),self.payload(enabled=''))
        self.assertNotIn(self.guide.slug,[g['id'] for g in self.client.get(reverse('assistant_guides')).json()])
        response=self.client.post(reverse('assistant_edit',args=[self.guide.pk]),self.payload())
        self.assertContains(response,'Outro administrador actualizou')
        self.guide.refresh_from_db();self.assertFalse(self.guide.enabled);self.assertEqual(self.guide.revision,2)

    def test_create_and_validate_route_and_steps(self):
        self.client.force_login(self.admin)
        data=self.payload(slug='novo-modulo',**{'steps-INITIAL_FORMS':'0'})
        self.assertEqual(self.client.post(reverse('assistant_create'),data).status_code,302)
        self.assertTrue(self.Guide.objects.filter(slug='novo-modulo').exists())
        for extra in [{'route':'https://example.com'}, {'steps-0-target':'script:eval()'}, {'steps-0-DELETE':'on'}]:
            response=self.client.post(reverse('assistant_create'),self.payload(slug='invalid',**{'steps-INITIAL_FORMS':'0'},**extra))
            self.assertEqual(response.status_code,200)
            self.assertFalse(self.Guide.objects.filter(slug='invalid').exists())

    def test_all_seeded_guides_can_be_saved_without_changing_steps(self):
        self.client.force_login(self.admin)
        for g in self.Guide.objects.all():
            data=self.payload(g,route=g.route,title=g.title)
            if g.staff_only:data['staff_only']='on'
            data['steps-TOTAL_FORMS']=str(len(g.steps));data['steps-INITIAL_FORMS']=str(len(g.steps))
            for i,step in enumerate(g.steps):data.update({f'steps-{i}-text':step['text'],f'steps-{i}-target':step['target'] or ''})
            with self.subTest(guide=g.slug):self.assertEqual(self.client.post(reverse('assistant_edit',args=[g.pk]),data).status_code,302)

    def test_guides_require_login_and_do_not_accept_writes(self):
        self.assertEqual(self.client.get(reverse('assistant_guides')).status_code,302)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(reverse('assistant_guides'),{}).status_code,405)
        page=self.client.get(reverse('planning'))
        self.assertNotContains(page,'Administrar assistente')

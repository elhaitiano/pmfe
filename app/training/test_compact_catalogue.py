"""Presentation-only catalogue regressions with synthetic data."""
import json
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.urls import reverse,resolve
from .models import Course,AuditEvent
from .catalogue_import import digest

class Cards(HTMLParser):
    def __init__(self,html):
        super().__init__();self.cards=[];self.current=None;self.depth=0;self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='article' and 'plan-course--compact' in attrs.get('class',''):
            self.current={'main':[],'details':[],'links':[]};self.cards.append(self.current)
        if self.current is not None:
            if tag=='details':self.depth+=1
            if tag=='a':self.current['links'].append((attrs.get('href'),bool(self.depth)))
    def handle_data(self,text):
        if self.current is not None:self.current['details' if self.depth else 'main'].append(text)
    def handle_endtag(self,tag):
        if self.current is not None:
            if tag=='details':self.depth-=1
            if tag=='article':self.current=None

@override_settings(PMF_HISTORICAL_ENABLED=True)
class CompactCatalogueTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('synthetic-compact',password='synthetic')
        meta={'id':'SYN-COMPACT','title':'Synthetic catalogue training with a complete long designation preserved for details','days':'10','hours':'6','code':'SYN-ORIGINAL','area':'','origin':'Synthetic documented source'}
        self.course=Course.objects.create(title=meta['title'],source_system='epal-proagua-catalogo',source_action_id=meta['id'],source_internal_code='SYN-ORIGINAL',source_metadata=meta,source_digest=digest(meta),default_days=10,default_hours=6,revision=2,validation_status='pending',validation_notes='Synthetic original validation note')
        AuditEvent.objects.create(actor='synthetic',action='DURATION_ACTION',detail=json.dumps({'pk':self.course.pk,'source_action_id':self.course.source_action_id,'days_after':10,'hours_after':'6','revision_after':2,'reference':'Synthetic documentary reference','reason':'Synthetic justification'}))
        self.count=2
        def counts(courses):
            for c in courses:c.historical_count=self.count
        self.mock=patch('training.historical.attach_counts',side_effect=counts);self.mock.start();self.addCleanup(self.mock.stop)
        self.client.force_login(self.admin)

    def page(self):return self.client.get(reverse('planning'))

    def test_only_essential_data_outside_disclosure(self):
        response=self.page();card=Cards(response.content.decode()).cards[0]
        main=' '.join(card['main']);detail=' '.join(card['details'])
        self.assertIn('Duração verificada',main);self.assertIn('60 H',main);self.assertIn('Pendente',main)
        for text in ['Dados verificados','Código aprovado','Área por definir','Synthetic documentary reference','Editar acção','Synthetic original validation note']:
            self.assertNotIn(text,main);self.assertIn(text,detail)
        self.assertIn(self.course.title,detail)

    def test_existing_historical_route_only_when_documented_count_positive(self):
        card=Cards(self.page().content.decode()).cards[0]
        links=[href for href,in_detail in card['links'] if not in_detail]
        self.assertEqual(links,[reverse('historical_index')+'?action=SYN-COMPACT'])
        self.assertEqual(resolve(links[0].split('?')[0]).url_name,'historical_index')
        self.count=0;card=Cards(self.page().content.decode()).cards[0]
        self.assertFalse([href for href,in_detail in card['links'] if not in_detail])

    def test_unknown_historical_access_not_invented_zero(self):
        with override_settings(PMF_HISTORICAL_ENABLED=False):
            response=self.page();self.assertContains(response,'Consulta não activada ou sem permissão');self.assertNotContains(response,'plan-consult-groups')

    def test_administrative_links_retained_inside_details(self):
        card=Cards(self.page().content.decode()).cards[0]
        self.assertIn((reverse('course_edit',args=[self.course.pk]),True),card['links'])
        self.assertNotIn((reverse('group_create',args=[self.course.pk]),True),card['links'])
        self.assertNotIn((reverse('course_delete',args=[self.course.pk]),True),card['links'])

    def test_unproved_duration_has_no_verified_marker(self):
        AuditEvent.objects.all().delete();card=Cards(self.page().content.decode()).cards[0]
        self.assertNotIn('Duração verificada',' '.join(card['main']))

    def test_formal_and_operational_states_unchanged(self):
        before=list(Course.objects.values());events=list(AuditEvent.objects.values());self.page()
        self.assertEqual(before,list(Course.objects.values()));self.assertEqual(events,list(AuditEvent.objects.values()))
        self.course.refresh_from_db();self.assertFalse(self.course.can_create_groups)

    def test_browser_responsive_disclosure_and_keyboard(self):
        from playwright.sync_api import sync_playwright
        browser_path=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
        if not browser_path.exists():self.skipTest('Edge indisponível')
        html=self.page().content.decode()
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=str(browser_path),headless=True)
            try:
                page=browser.new_page();page.route('**/*',lambda route:route.abort());page.set_content(html,wait_until='domcontentloaded')
                for css in ('tailwind.css','platform-original.css','adapters.css','batch.css','planning.css'):
                    page.add_style_tag(path=str(settings.BASE_DIR/'static/pmf'/css))
                card=page.locator('.plan-course--compact').first;summary=card.locator(':scope > details > summary')
                for width in (360,768,1024,1440):
                    page.set_viewport_size({'width':width,'height':1000})
                    self.assertFalse(card.locator('.plan-technical').is_visible())
                    self.assertTrue(card.locator('.plan-consult-groups').is_visible())
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                    summary.focus();page.keyboard.press('Enter');self.assertTrue(card.locator('.plan-technical').is_visible())
                    self.assertTrue(card.get_by_role('link',name='Editar acção',exact=False).is_visible())
                    page.keyboard.press('Enter');self.assertFalse(card.locator('.plan-technical').is_visible())
            finally:browser.close()
